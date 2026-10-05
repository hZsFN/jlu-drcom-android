"""密码保护 —— Android 侧实现（替代 Windows 的 DPAPI）。

Windows 版走 CryptProtectData：密钥由登录会话派生，换机器、换用户就解不开，
连程序自己都还原不了——这正是「记住密码」想要的性质。

Android 上没有对应物：

* Keystore 是硬件 backed 的，但只有 Java 侧能碰。Chaquopy 要绕过去得写一层代理类，
  会把一个两百行的模块撑成八百行；
* 而且 Keystore 里的密钥在卸载重装 / 恢复出厂后一样失效，性质并不比现在这档更好；
* 原版这里还有一条 cryptography(Fernet) 分支，Chaquopy 默认不带这个包，
  为了加密一个校园网密码把它拖进来不划算。

所以这里做一档**强度如实标注**的本地方案：

    随机密钥文件（数据目录下，0600 权限）
      → HMAC-SHA256 计数器模式生成密钥流 → 与明文异或
      → 头部 8 字节随机 nonce，尾部 16 字节 HMAC 校验（防篡改、防换设备解密）

它挡的是「adb 备份 / 云同步 / 顺手拷走配置文件」；
挡不住「拿到这台设备、能在上面跑程序的人」。够用，但不假装是 DPAPI。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ProtectionResult",
    "protect",
    "unprotect",
    "protection_backend_name",
]

#: 标记字节：一眼看出密文出自哪一档，也防止把别家的 blob 喂进来。
_TAG_ANDROID = b"A1:"

_BACKEND = "Android 本地加密（HMAC-SHA256 流 + 校验）"
_NONCE_LEN = 8
_TAG_LEN = 16


class ProtectError(Exception):
    """Raised when a stored secret cannot be recovered."""


@dataclass(frozen=True)
class ProtectionResult:
    """A protected (or unprotected) secret plus how it was produced."""

    value: str
    backend: str
    degraded: bool = False

    @property
    def warning(self) -> str:
        if not self.degraded:
            return ""
        return (
            f"密码以「{self.backend}」保存在本机，密钥文件与配置文件同目录。"
            "它能挡住随手拷走配置的场景，但拿到这台设备的人仍可能还原它。"
        )


def protection_backend_name() -> str:
    return _BACKEND


def _machine_key(key_path: Path) -> bytes:
    """Load or create the local 32-byte key file."""
    try:
        if key_path.exists():
            raw = key_path.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
    except OSError:
        pass

    key = os.urandom(32)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # 只读文件系统上退化成进程内临时密钥：本次运行内仍能自洽地加解密
        pass
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 计数器模式——纯标准库，不需要任何第三方加密包。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + struct.pack(">I", counter), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def protect(plaintext: str, *, key_path: Path) -> ProtectionResult:
    """Protect a secret for storage. Returns the base64 body plus metadata."""
    data = plaintext.encode("utf-8")
    key = _machine_key(key_path)
    nonce = os.urandom(_NONCE_LEN)
    body = bytes(a ^ b for a, b in zip(data, _keystream(key, nonce, len(data))))
    tag = hmac.new(key, _TAG_ANDROID + nonce + body, hashlib.sha256).digest()[:_TAG_LEN]
    blob = _TAG_ANDROID + nonce + body + tag
    return ProtectionResult(base64.b64encode(blob).decode("ascii"), _BACKEND)


def unprotect(stored: str, *, key_path: Path) -> ProtectionResult:
    """Recover a secret previously produced by :func:`protect`."""
    if not stored:
        return ProtectionResult("", _BACKEND)

    try:
        blob = base64.b64decode(stored, validate=True)
    except Exception as exc:
        raise ProtectError("密码字段不是合法的 Base64，配置可能已损坏") from exc

    if blob.startswith(b"D1:"):
        raise ProtectError("该密码由 Windows DPAPI 加密，请在原来那台 Windows 机器上重新输入一次")

    if not blob.startswith(_TAG_ANDROID):
        raise ProtectError("无法识别的密码存储格式，请重新输入密码")

    payload = blob[len(_TAG_ANDROID) :]
    if len(payload) < _NONCE_LEN + _TAG_LEN:
        raise ProtectError("密码字段长度不对，配置可能已损坏")

    nonce = payload[:_NONCE_LEN]
    body = payload[_NONCE_LEN:-_TAG_LEN]
    tag = payload[-_TAG_LEN:]

    key = _machine_key(key_path)
    expected = hmac.new(key, _TAG_ANDROID + nonce + body, hashlib.sha256).digest()[:_TAG_LEN]
    if not hmac.compare_digest(tag, expected):
        raise ProtectError("密码校验不通过（密钥文件被删或被换，或配置被改过）")

    data = bytes(a ^ b for a, b in zip(body, _keystream(key, nonce, len(body))))
    try:
        return ProtectionResult(data.decode("utf-8"), _BACKEND)
    except UnicodeDecodeError as exc:
        raise ProtectError("解密结果不是合法文本，请重新输入密码") from exc
