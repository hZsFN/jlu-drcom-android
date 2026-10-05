"""Android 侧入口 —— Kotlin 只通过这里跟 Python 打交道。

调用链：

    MainActivity / AuthForegroundService            （Kotlin）
        ↓  PythonBridge
    android_main.start() / stop() / status()         （本文件）
        ↓
    drcom.engine.AuthEngine                          （认证状态机，原样复用）
      + secrets_store / netiface / binding           （Android 版平台层）

这一层只做三件事：把配置读出来、把引擎跑起来、把状态和日志攒成 Kotlin 能直接读
的字典。业务逻辑一律不要放这儿——引擎跑在后台线程里，这里每个入口都要假定会被
任意线程调用。
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any

from drcom.config import Account, ConfigStore, default_data_dir
from drcom.engine import AuthEngine, EngineEvent, EngineState
from drcom.logbus import LogBus
from drcom.stats import StatsStore

__all__ = [
    "default_data_dir",
    "list_interfaces",
    "recent_log",
    "start",
    "status",
    "stop",
]

_LOCK = threading.RLock()
_ENGINE: AuthEngine | None = None
_BUS: LogBus | None = None

_LOG: list[str] = []
_LOG_LIMIT = 400

#: 兜底的 MAC —— 正常情况下走不到这里。
#: 界面上的预填值由 Kotlin 从 local.properties 的 defaultMac 传进来（那才是真值），
#: 这里留空，免得某台机器的 MAC 被写进公开仓库。
DEFAULT_MAC = ""

_STATE: dict[str, Any] = {
    "running": False,
    "status": "未启动",
    "detail": "",
    "account": "",
    "ip": "",
    "online": False,
    "since": 0.0,
    "online_since": 0.0,
    "error": "",
}


def _normalize_mac(raw: str) -> str:
    """把 AA-BB-CC-DD-EE-FF / aa:bb:cc:dd:ee:ff / aabbccddeeff 统一成一种写法。

    认证服务器对 MAC 的写法敏感，而它多半是人手输进设置里的，很容易带错分隔符，
    所以在入口处一次性归一化，别让格式问题伪装成"密码错"。
    """
    hex_only = "".join(ch for ch in raw if ch.isalnum())
    if len(hex_only) != 12:
        return ""
    return "-".join(hex_only[i : i + 2].upper() for i in range(0, 12, 2))


def _note(line: str) -> None:
    _LOG.append(f"[{time.strftime('%H:%M:%S')}] {line}")
    if len(_LOG) > _LOG_LIMIT:
        del _LOG[:-_LOG_LIMIT]


def _set(**kwargs: Any) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def _to_plain(value: Any) -> Any:
    """把 Chaquopy 塞过来的 Java 对象拗成 Python 原生类型。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    return str(value)


def _to_plain_dict(value: Any) -> dict[str, Any]:
    """把 Kotlin 传来的 Map 变成普通 dict。

    Chaquopy **不会**替我们做这个转换：Java 的 Map 到了 Python 侧仍然是个 Java 对象，
    直接 dict(config) 会抛 ``TypeError: 'LinkedHashMap' object is not iterable``——
    真机上就是这么炸的（android_main.py:111）。所以这里显式问它要 keySet，再逐个取值。

    反方向（Python dict → java.util.Map）同样不自动转，也不要用。
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)

    keys = None
    for name in ("keySet", "keys"):
        getter = getattr(value, name, None)
        if callable(getter):
            try:
                keys = getter()
                break
            except Exception:
                continue
    if keys is None:
        try:
            return dict(value)
        except Exception:
            return {}

    try:
        key_list = list(keys.toArray())
    except Exception:
        key_list = list(keys)

    out: dict[str, Any] = {}
    for key in key_list:
        try:
            raw = value.get(key)
        except Exception:
            raw = None
        out[str(key)] = _to_plain(raw)
    return out


def _on_event(event: EngineEvent) -> None:
    """引擎每变一次状态就往这里推一条。回调跑在引擎线程上，必须短。"""
    state = event.state
    message = event.message or state.value
    _note(f"{state.value}: {message}")

    update: dict[str, Any] = {
        "status": message,
        "detail": event.detail or event.advice or "",
    }
    if event.ip:
        update["ip"] = event.ip
    if state is EngineState.ONLINE:
        update["online"] = True
        update["online_since"] = time.time()
    elif state in (EngineState.FATAL, EngineState.STOPPED):
        update["online"] = False
    if state is EngineState.FATAL:
        update["error"] = message
    _set(**update)


def start(config: dict[str, Any] | None = None) -> bool:
    """按配置启动认证。返回是否成功起步（不保证认证成功）。"""
    global _ENGINE, _BUS

    config = _to_plain_dict(config)
    try:
        with _LOCK:
            if _ENGINE is not None and _ENGINE.is_running():
                _note("已经在跑，忽略这次启动请求")
                return True

        account_name = str(config.get("account", "")).strip()
        password = str(config.get("password", ""))
        if not account_name:
            _note("缺少账号，无法启动")
            _set(status="缺少账号", error="请先填写学号")
            return False
        if not password:
            _note("缺少密码，无法启动")
            _set(status="缺少密码", error="请先填写密码")
            return False

        data_dir = default_data_dir()
        store = ConfigStore(data_dir)
        store.ensure_dirs()
        app_config = store.load()

        account = store.find_by_account_name(account_name)
        if account is None:
            account = Account(account=account_name)
            store.add_account(account, make_active=True)
        else:
            app_config.active_account_id = account.id

        mac = _normalize_mac(str(config.get("mac", ""))) or _normalize_mac(account.mac)
        if mac:
            account.mac = mac
        else:
            # 兜底：认证只认电脑那块有线网卡，界面和配置都没给时用预置值。
            account.mac = DEFAULT_MAC
            _note(f"界面没有提供 MAC，改用预置值 {DEFAULT_MAC}")

        server = str(config.get("server", "")).strip()
        if server:
            app_config.auth.server = server
        port = config.get("port")
        if isinstance(port, int) and port > 0:
            app_config.auth.port = port
            app_config.auth.bind_port = port
        store.save()

        # set_password 的返回值是「是否降级」，不是「是否成功」——语义来自 Windows 版：
        # 它优先用 DPAPI，实在不行才退回可逆混淆，并把那次结果标成 degraded。
        # Android 版走的是 HMAC 流加密，落不到降级分支，所以这里常态就是 False。
        try:
            degraded = store.set_password(account.id, password)
        except Exception as exc:  # 加密后端真出问题时不该拦住登录
            degraded = False
            _note(f"密码没能加密保存（{exc}），本次仍用内存里的密码继续")
        if degraded:
            _note("注意：" + (store.password_backend_note(account.id) or "密码以可逆方式保存"))
        store.save()  # set_password 只改内存对象，得再存一次才会落盘

        bus = LogBus(
            log_dir=data_dir / "logs",
            level=app_config.logging.level,
            mask_accounts=app_config.logging.mask_accounts,
            protocol_hex=app_config.logging.protocol_hex,
            keep_days=app_config.logging.keep_days,
            max_file_mb=app_config.logging.max_file_mb,
        )
        stats = StatsStore(data_dir / "stats.json", keep_days=120)

        engine = AuthEngine(
            config=app_config,
            account=account,
            password=password,
            log=bus,
            stats=stats,
        )
        engine.subscribe(_on_event)

        with _LOCK:
            _BUS = bus
            _ENGINE = engine

        _set(
            running=True,
            account=account_name,
            status="正在启动",
            error="",
            since=time.time(),
            online_since=0.0,
        )
        _note(f"启动认证：{account.display_name}，MAC={account.mac}")
        engine.start()  # 起工作线程后立刻返回
        return True
    except Exception:
        text = traceback.format_exc()
        _note("start() 异常：" + text)
        _set(status="启动失败", error=text.strip().splitlines()[-1], running=False)
        return False


def stop() -> bool:
    """请求引擎停机，最多等 5 秒。"""
    with _LOCK:
        engine = _ENGINE
    if engine is None:
        _set(running=False, status="已停止", online=False)
        return True
    try:
        engine.stop()
        engine.join(timeout=5.0)
        _note("已请求停止认证")
        _set(running=False, status="已停止", online=False)
        return True
    except Exception:
        _note("stop() 异常：" + traceback.format_exc())
        return False


def status() -> dict[str, Any]:
    """给界面轮询用的状态快照。"""
    with _LOCK:
        snapshot = dict(_STATE)
        engine = _ENGINE

    if engine is None:
        snapshot["engine_state"] = "idle"
        snapshot["uptime"] = 0
    else:
        snapshot["engine_state"] = engine.state.value
        snapshot["uptime"] = round(engine.uptime_seconds(), 1)
        # 注意：is_running / is_online 在原版 engine.py 里是 @property，不是方法，
        # 加括号会得到 "'bool' object is not callable"。
        snapshot["running"] = bool(engine.is_running)
        snapshot["online"] = bool(engine.is_online)
    return snapshot


def recent_log(n: int = 60) -> str:
    try:
        count = max(1, int(n))
    except (TypeError, ValueError):
        count = 60
    with _LOCK:
        return "\n".join(_LOG[-count:])


def list_interfaces() -> list[dict[str, Any]]:
    """给界面用：这台机器上现在有哪些网卡（不是认证要用的那块）。"""
    try:
        from drcom.netiface import list_interfaces as _list
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for item in _list():
        out.append(
            {
                "name": item.name,
                "kind": item.kind,
                "mac": item.mac,
                "isUp": item.is_up,
                "bytesIn": item.bytes_in,
                "bytesOut": item.bytes_out,
            }
        )
    return out
