"""网卡枚举 —— Android 侧实现。

Windows 版走 iphlpapi 的 ``GetIfTable2`` / ``GetAdaptersAddresses``：一次调用就能
拿到每块网卡的「类型 / 状态 / 速率 / 字节计数器」。Android 上没有这套 API，
但 Linux 把同样的东西摊在几个文件里，读文件就够：

    /sys/class/net/<if>/address      MAC 地址
    /sys/class/net/<if>/operstate    up / down / unknown
    /sys/class/net/<if>/mtu          MTU
    /sys/class/net/<if>/speed        链路速率（Mbps，很多网卡不提供）
    /sys/class/net/<if>/type         ARPHRD_* 编号
    /proc/net/dev                    收发字节 / 包 / 错误计数

**这个模块不决定「认证用哪个 MAC」**：校园网账号绑定的是入学时登记的那台机器
的网卡地址（在配置里，由用户填写或预置），跟当前上网设备无关。这里列出的网卡
只用于两件事——挑出口、以及在界面上告诉用户「你现在挂在哪个网上」。
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "InterfaceInfo",
    "get_default_route_ip",
    "list_interfaces",
    "pick_relevant_interface",
]

#: 与 Windows 版共用一套 IF_TYPE_* 编号（netioapi.h），
#: 这样上游代码里的 if_type 判断和界面文案不用分平台写两份。
_IF_TYPE_ETHERNET_CSMACD = 6
_IF_TYPE_SOFTWARE_LOOPBACK = 24
_IF_TYPE_IEEE80211 = 71
_IF_TYPE_TUNNEL = 131

_ARPHRD_ETHER = 1
_ARPHRD_LOOPBACK = 772
_ARPHRD_NONE = 65534

_SYS_NET = Path("/sys/class/net")
_PROC_NET_DEV = Path("/proc/net/dev")

#: 名字带这些前缀的基本不是「校园网出口那块网卡」。
_VIRTUAL_HINTS = (
    "lo", "dummy", "tun", "tap", "ppp", "sit", "ip6tnl", "gre", "erspan",
)


@dataclass
class InterfaceInfo:
    """A snapshot of one interface."""

    name: str
    description: str = ""
    index: int = 0
    mtu: int = 0
    speed_bps: int = 0
    if_type: int = 0
    oper_status: int = 0
    mac: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    packets_in: int = 0
    packets_out: int = 0
    errors_in: int = 0
    errors_out: int = 0
    is_up: bool = False

    @property
    def kind(self) -> str:
        if self.if_type == _IF_TYPE_IEEE80211:
            return "无线"
        if self.if_type == _IF_TYPE_ETHERNET_CSMACD:
            return "以太网"
        if self.if_type == _IF_TYPE_TUNNEL:
            return "隧道/VPN"
        if self.if_type == _IF_TYPE_SOFTWARE_LOOPBACK:
            return "回环"
        return "其它"

    @property
    def label(self) -> str:
        return self.name or self.description or f"if{self.index}"


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return default


def _read_int(path: Path, default: int = 0) -> int:
    try:
        return int(_read_text(path))
    except ValueError:
        return default


def _map_if_type(sysfs_type: int, name: str) -> int:
    """sysfs 的 type 是 ARPHRD_*，和 Windows 的 IF_TYPE_* 不是同一套编号。"""
    if sysfs_type == _ARPHRD_LOOPBACK:
        return _IF_TYPE_SOFTWARE_LOOPBACK
    if sysfs_type == _ARPHRD_NONE:
        return _IF_TYPE_TUNNEL
    if name.startswith(("wlan", "wifi", "ap")):
        return _IF_TYPE_IEEE80211
    if name.startswith(("rmnet", "ccmni", "pdp")):
        # 蜂窝数据。界面上写成「以太网」比写成「其它」更不容易让人误会。
        return _IF_TYPE_ETHERNET_CSMACD
    if sysfs_type == _ARPHRD_ETHER:
        return _IF_TYPE_ETHERNET_CSMACD
    return 0


def _dev_counters() -> dict[str, tuple[int, int, int, int, int, int]]:
    """解析 /proc/net/dev → name: (rx_bytes, rx_pkts, rx_errs, tx_bytes, tx_pkts, tx_errs)."""
    out: dict[str, tuple[int, int, int, int, int, int]] = {}
    try:
        text = _PROC_NET_DEV.read_text(encoding="ascii", errors="replace")
    except OSError:
        return out

    for line in text.splitlines()[2:]:  # 前两行是表头
        head, _, rest = line.partition(":")
        name = head.strip()
        fields = rest.split()
        if not name or len(fields) < 16:
            continue
        try:
            out[name] = (
                int(fields[0]),
                int(fields[1]),
                int(fields[2]),
                int(fields[8]),
                int(fields[9]),
                int(fields[10]),
            )
        except ValueError:
            continue
    return out


def _index_map() -> dict[str, int]:
    """name → ifindex。if_nameindex() 在 Android 上要求 INTERNET 权限。"""
    try:
        return {name: index for index, name in socket.if_nameindex()}
    except OSError:
        return {}


def list_interfaces() -> list[InterfaceInfo]:
    """Enumerate interfaces from sysfs/procfs."""
    counters = _dev_counters()
    indexes = _index_map()

    try:
        names = sorted(p.name for p in _SYS_NET.iterdir())
    except OSError:
        names = sorted(counters)

    out: list[InterfaceInfo] = []
    for name in names:
        base = _SYS_NET / name
        mac = _read_text(base / "address")
        operstate = _read_text(base / "operstate", "unknown")
        speed_mbps = _read_int(base / "speed", -1)
        rx_b, rx_p, rx_e, tx_b, tx_p, tx_e = counters.get(name, (0, 0, 0, 0, 0, 0))

        out.append(
            InterfaceInfo(
                name=name,
                description=name,
                index=indexes.get(name, 0),
                mtu=_read_int(base / "mtu"),
                speed_bps=speed_mbps * 1_000_000 if speed_mbps > 0 else 0,
                if_type=_map_if_type(_read_int(base / "type"), name),
                oper_status=1 if operstate == "up" else 0,
                mac=mac.upper() if mac and mac != "00:00:00:00:00:00" else "",
                bytes_in=rx_b,
                bytes_out=tx_b,
                packets_in=rx_p,
                packets_out=tx_p,
                errors_in=rx_e,
                errors_out=tx_e,
                is_up=operstate == "up",
            )
        )
    return out


def get_default_route_ip(server: str = "10.100.61.3", port: int = 61440) -> str:
    """Ask the kernel which local address it would use to reach the auth server.

    把 UDP socket ``connect`` 到目标地址不会真的发包——只是让内核按路由表挑一个
    源地址，再把挑中的本地地址读回来。这在 Android 上同样有效，而且比解析
    ``/proc/net/route`` 靠谱：VPN、多出口、策略路由都已经被内核算进去了。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(1.0)
        sock.connect((server, port))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def _interface_addresses() -> dict[str, list[str]]:
    """name → IPv4 地址（逐个网卡 ioctl 问，Android 没有 getifaddrs 的 py 绑定）。"""
    mapping: dict[str, list[str]] = {}
    try:
        import fcntl
    except ImportError:  # pragma: no cover - 桌面测试时才可能走到
        return mapping

    for name in _index_map():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            packed = struct.pack("256s", name[:15].encode("ascii"))
            res = fcntl.ioctl(sock.fileno(), 0x8915, packed)  # SIOCGIFADDR
            mapping[name] = [socket.inet_ntoa(res[20:24])]
        except OSError:
            continue
        finally:
            sock.close()
    return mapping


def pick_relevant_interface(
    *, server: str = "10.100.61.3", port: int = 61440
) -> InterfaceInfo | None:
    """Best guess at the interface carrying campus traffic."""
    interfaces = [i for i in list_interfaces() if i.if_type != _IF_TYPE_SOFTWARE_LOOPBACK]
    if not interfaces:
        return None

    route_ip = get_default_route_ip(server, port)
    if route_ip.startswith("127."):
        # 指到本机测试服务器时，这条路由完全说明不了该用哪块网卡。
        route_ip = ""
    addrs = _interface_addresses() if route_ip else {}

    def score(item: InterfaceInfo) -> int:
        points = 0
        if route_ip and route_ip in addrs.get(item.name, ()):
            points += 1000  # 「内核会从我这里走」是最强的信号
        if item.if_type == _IF_TYPE_IEEE80211:
            points += 60  # 手机上 Wi-Fi 才是校园网出口
        elif item.if_type == _IF_TYPE_ETHERNET_CSMACD:
            points += 40
        elif item.if_type == _IF_TYPE_TUNNEL:
            points -= 200
        points += 20 if item.mac else -150
        if item.is_up:
            points += 10
        if item.bytes_in or item.bytes_out:
            points += 30
        for hint in _VIRTUAL_HINTS:
            if item.name.startswith(hint):
                points -= 120
                break
        return points

    ranked = sorted(interfaces, key=score, reverse=True)
    best = ranked[0]
    return best if score(best) > -100 else interfaces[0]
