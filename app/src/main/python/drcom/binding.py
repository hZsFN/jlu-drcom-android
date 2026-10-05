"""认证 socket 的获取 —— Android 侧实现。

Dr.COM 的认证报文是从**源端口 61440** 发出去的：服务器按「源 IP + 源端口 + MAC」
回包，所以这个端口不是随便挑的，规范里写死了。Windows 版围绕它做了一整套诊断
（谁占着端口、是不是被 Hyper-V 的 excludedportrange 圈走了、netsh 怎么查），
因为 Windows 上有 WSL、Hyper-V、杀软一堆东西会偷端口。

Android 上没这么复杂：

* Linux 的临时端口范围默认是 32768–60999，61440 落在范围之外，系统不会随手把它
  分给别的进程；
* 普通应用绑定 1024 以上的端口不需要 root。

所以这里保留同样的调用契约，把自愈阶梯砍到真正用得上的几级：

    1. ``SO_REUSEADDR`` + 直接 bind（规范路径）
    2. 短暂重试——上一个进程刚退出，socket 还没被内核回收
    3. 换绑具体网卡地址（``0.0.0.0`` 被策略拒绝时）
    4. 可选：换备选源端口（偏离规范，默认关闭）

上游 ``engine.py`` 只用到 ``bind_udp_socket`` / ``BoundSocket`` / ``BindFailure``
这三个名字，其余是给排障界面看的。
"""

from __future__ import annotations

import errno
import socket
import time
from dataclasses import dataclass, field

__all__ = [
    "BindDiagnosis",
    "BindFailure",
    "BoundSocket",
    "bind_udp_socket",
]


@dataclass
class BoundSocket:
    """A ready-to-use UDP socket plus metadata about how it was obtained."""

    sock: socket.socket
    local_port: int
    bind_address: str
    #: True when we had to deviate from the spec'd 61440
    used_fallback_port: bool = False
    heal_notes: list[str] = field(default_factory=list)


@dataclass
class BindDiagnosis:
    """Why the bind failed, phrased for a phone screen."""

    port: int
    errno: int
    headline: str
    explanation: str
    kind: str = "unknown"
    advice: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [self.headline, "", self.explanation]
        if self.advice:
            lines.append("")
            lines.extend(f"· {item}" for item in self.advice)
        return "\n".join(lines)


class BindFailure(Exception):
    """Raised when the socket could not be bound; carries the diagnosis."""

    def __init__(self, diagnosis: BindDiagnosis) -> None:
        self.diagnosis = diagnosis
        super().__init__(diagnosis.headline)


def _errno_of(exc: OSError) -> int:
    return exc.errno if exc.errno is not None else 0


def _explain(port: int, code: int) -> tuple[str, str, str, list[str]]:
    """(kind, headline, explanation, advice) for a bind errno."""
    if code == errno.EADDRINUSE:
        return (
            "in_use",
            f"端口 {port} 已被占用",
            f"源端口 {port} 是认证规范要求的，服务器按它回包。现在有别的进程正占着它。",
            [
                "关掉另一个正在运行的认证客户端（同一台设备上通常只有一个能工作）",
                "退出后等几秒再试——上一个进程的 socket 需要一点时间被内核回收",
            ],
        )
    if code in (errno.EACCES, errno.EPERM):
        return (
            "denied",
            f"没有权限绑定端口 {port}",
            "系统拒绝了这个绑定请求。普通应用绑定 1024 以上的端口一般不需要 root。",
            [
                "检查系统是不是把应用的后台网络权限掐了",
                "如果开着 VPN / 广告拦截 / 防火墙类应用（Clash、AdGuard 等），先退出再试",
            ],
        )
    if code == errno.EADDRNOTAVAIL:
        return (
            "no_address",
            "要绑定的网卡地址不存在",
            "试图绑定到一个当前设备上没有的 IP——通常是 Wi-Fi 刚切换、本机 IP 变了。",
            ["确认已经连上校园 Wi-Fi 再试"],
        )
    return (
        "unknown",
        f"端口 {port} 绑定失败（errno {code}）",
        "系统没有给出更具体的原因。",
        ["先确认已连上校园网络", "重启一次应用再试"],
    )


def _try_bind(port: int, address: str, *, allow_reuse: bool) -> tuple[socket.socket | None, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if allow_reuse:
            # 必须在 bind() 之前设置——规范 6.2。
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        return sock, 0
    except OSError as exc:
        code = _errno_of(exc)
        sock.close()
        return None, code


def _finish_socket(sock: socket.socket, timeout_ms: int) -> None:
    """Apply the receive timeout, then widen the buffers a little."""
    sock.settimeout(max(0.05, timeout_ms / 1000.0))
    for option in ("SO_RCVBUF", "SO_SNDBUF"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, option), 64 * 1024)
        except OSError:
            pass


def _free_nearby(port: int, *, limit: int = 12) -> list[int]:
    """Ports just above ``port`` that can be bound right now."""
    found: list[int] = []
    for offset in range(1, 200):
        candidate = port + offset
        if candidate > 65535:
            break
        sock, _ = _try_bind(candidate, "0.0.0.0", allow_reuse=True)
        if sock is not None:
            sock.close()
            found.append(candidate)
            if len(found) >= limit:
                break
    return found


def bind_udp_socket(
    port: int,
    *,
    address: str = "0.0.0.0",
    timeout_ms: int = 3000,
    allow_alternate_port: bool = False,
    alternate_candidates: int = 12,
    self_heal: bool = True,
) -> BoundSocket:
    """Bind the auth socket, self-healing where possible.

    :raises BindFailure: with a full :class:`BindDiagnosis` when nothing worked.
    """
    notes: list[str] = []
    last_code = 0

    # --- 1 & 2：规范路径，带两次重试 ------------------------------------
    for attempt in range(3):
        sock, code = _try_bind(port, address, allow_reuse=True)
        if sock is not None:
            _finish_socket(sock, timeout_ms)
            return BoundSocket(sock, port, address)
        last_code = code or last_code
        if attempt < 2:
            time.sleep(0.4 * (attempt + 1))

    # --- 3：0.0.0.0 被拒时，改绑具体网卡地址 ----------------------------
    if self_heal:
        from .netiface import get_default_route_ip

        local_ip = get_default_route_ip()
        if local_ip and not local_ip.startswith("127."):
            sock, code = _try_bind(port, local_ip, allow_reuse=True)
            if sock is not None:
                notes.append(f"已改为绑定具体网卡地址 {local_ip}（0.0.0.0 绑定被拒绝）。")
                _finish_socket(sock, timeout_ms)
                return BoundSocket(sock, port, local_ip, heal_notes=notes)
            last_code = code or last_code

    # --- 4：备选源端口（可选，偏离规范）---------------------------------
    if allow_alternate_port:
        for candidate in _free_nearby(port, limit=alternate_candidates):
            sock, _ = _try_bind(candidate, address, allow_reuse=True)
            if sock is not None:
                notes.append(
                    f"注意：已改用备选源端口 {candidate}（规范要求 {port}）。"
                    "Dr.COM 服务器一般按报文源端口回包，因此通常可用；若认证失败请关闭此自愈选项。"
                )
                _finish_socket(sock, timeout_ms)
                return BoundSocket(
                    sock, candidate, address, used_fallback_port=True, heal_notes=notes
                )

    kind, headline, explanation, advice = _explain(port, last_code)
    if notes:
        advice.insert(0, "；".join(notes))
    raise BindFailure(
        BindDiagnosis(
            port=port,
            errno=last_code,
            headline=headline,
            explanation=explanation,
            kind=kind,
            advice=advice,
        )
    )
