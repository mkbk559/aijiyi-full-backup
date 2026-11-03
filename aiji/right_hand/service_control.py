"""Local Windows service lifecycle for the operations page.

Everything returned here is a live probe: a service is "ready" only when its
port answers (or, for the QQ reverse link, when a real connection exists).
No cached truth beyond a two-second netstat snapshot.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from aiji.skeleton.paths import ROOT

SCRIPT = ROOT / "tools" / "lifecycle.ps1"
WATCHDOG_SCRIPT = ROOT / "tools" / "ensure_console.ps1"
STATE_FILE = ROOT / "data" / "service_state.txt"
LOG_FILE = ROOT / "data" / "lifecycle.log"
WATCHDOG_TASK = "AIJiYi Console Watchdog"
TASK_FILE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Tasks" / WATCHDOG_TASK

# Dependency order: each entry only becomes useful after the previous one.
SERVICES = (
    ("napcat", "NapCat", 6099, "把本机 QQ 接管成 OneBot；端口在听表示注入层已就绪。"),
    ("onebot", "QQ 反向连接", 6199, "AstrBot 的 OneBot 服务端；QQ 登录并连上后才会出现 ESTABLISHED。"),
    ("astrbot", "AstrBot", 6185, "消息处理与插件宿主，AIJiYi 的回复都经它送出。"),
    ("aiji", "AIJiYi", 8787, "记忆与模型核心；本页面由它提供。"),
    ("presence", "实时状态", 8789, "WebSocket 状态广播，供前端与控制台实时同步。"),
)
PORTS = tuple((key, name, port) for key, name, port, _ in SERVICES)
# Ports whose server treats a bare TCP connect as a failed WebSocket handshake.
_WS_PORTS = frozenset(port for key, _name, port, _hint in SERVICES if key == "presence")

FIX_HINTS = {
    "napcat": "确认 QQ 已安装并能正常登录；点「启动整套服务」后仍失败时看日志与前置检查。",
    "onebot": "端口在听但没有 QQ 连接：确认 QQ 已登录、NapCat 已注入（首次登录可能要扫码）。",
    "astrbot": "检查 D:\\AstrBot 的 .venv 与 main.py；启动记录见 data\\astrbot.err.log。",
    "aiji": "点「启动整套服务」，或双击项目根目录的 启动AIJiYi.bat。",
    "presence": "随 AIJiYi 一起启动；只有它不在线时检查 8789 端口是否被占用。",
}

PREREQS = (
    ("Python 3.13", Path("D:/Python313/python.exe"), "AIJiYi 服务与查询脚本的运行环境", False),
    ("AstrBot 运行环境", Path("D:/AstrBot/.venv/Scripts/python.exe"), "AstrBot 用它启动 main.py", False),
    ("NapCat 注入器", Path("D:/NapCat/shell/NapCatWinBootMain.exe"), "把 QQ 客户端接管成 OneBot", False),
    ("QQ 客户端", Path("D:/QQNT/QQ.exe"), "需要保持登录状态", False),
    ("运行配置", ROOT / "config" / "runtime.json", "未创建时使用内置默认值", True),
)

VALID_ACTIONS = ("start", "stop", "restart", "watchdog-on", "watchdog-off")

_rows: dict[str, object] = {"at": 0.0, "rows": []}
_process_cache: dict[int, tuple[str, float]] = {}


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _run(args: list[str], timeout: float = 6.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False,
            encoding="utf-8", errors="replace",
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def _local_port(endpoint: str) -> int | None:
    head, _, tail = endpoint.rpartition(":")
    if not head or not tail.isdigit():
        return None
    return int(tail)


def _netstat_rows() -> list[list[str]]:
    """One `netstat -ano` snapshot, reused for two seconds by every probe."""
    now = time.monotonic()
    if now - float(_rows["at"]) < 2.0:
        return _rows["rows"]  # type: ignore[return-value]
    _, text = _run(["netstat", "-ano"], timeout=5.0)
    rows: list[list[str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() in ("TCP", "TCPV6"):
            rows.append(parts[:5])
    _rows.update(at=now, rows=rows)
    return rows


def _port_state(port: int) -> tuple[bool, int, int | None]:
    """(listening, established_connections, listening_pid)."""
    listening, connections, pid = False, 0, None
    for _proto, local, _remote, status, owner in _netstat_rows():
        if _local_port(local) != port:
            continue
        status = status.upper()
        if status == "LISTENING":
            listening = True
            try:
                pid = int(owner)
            except ValueError:
                pid = None
        elif status == "ESTABLISHED":
            connections += 1
    return listening, connections, pid


def _resolve_processes(pids: set[int]) -> dict[int, str]:
    """PID -> process name for every listening port.

    `tasklist` is not readable from every account/sandbox on this machine
    ("ERROR: Access denied"), while one PowerShell call for all PIDs is.
    """
    wanted = {pid for pid in pids if pid}
    missing = [pid for pid in wanted if _process_stale(pid)]
    if missing:
        script = ("Get-Process -Id " + ",".join(str(pid) for pid in missing) +
                  " | ForEach-Object { '{0} {1}' -f $_.Id, $_.ProcessName }")
        _, text = _run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", script], 8.0)
        now = time.monotonic()
        for pid in missing:
            _process_cache.setdefault(pid, ("", now))
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                _process_cache[int(parts[0])] = (parts[1], now)
    return {pid: _process_cache.get(pid, ("", 0.0))[0] for pid in wanted}


def _process_stale(pid: int) -> bool:
    entry = _process_cache.get(pid)
    return entry is None or time.monotonic() - entry[1] > 120


def _listening(port: int) -> bool:
    """Whether the port is in LISTENING state.

    netstat first: it answers without side effects. The raw TCP connect is only
    a fallback, and never for the presence WebSocket port — that server logs an
    empty connection as `opening handshake failed` + full traceback at ERROR
    level, so probing it from every status refresh floods the log
    (measured 2026-11-02: exactly one traceback per /api/operations/services).
    """
    listening = _port_state(port)[0]
    if listening or port in _WS_PORTS:
        return listening
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _established(port: int) -> bool:
    return _port_state(port)[1] > 0


def desired_state() -> str | None:
    try:
        text = STATE_FILE.read_text("utf-8", "replace").strip()
    except OSError:
        return None
    return text or None


def _startup_entry() -> str:
    appdata = os.environ.get("APPDATA") or ""
    if not appdata:
        return ""
    folder = Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"
    try:
        for item in folder.iterdir():
            if "aiji" in item.name.lower():
                return item.name
    except OSError:
        return ""
    return ""


def watchdog() -> dict:
    code, text = _run(["schtasks", "/Query", "/TN", WATCHDOG_TASK], timeout=8.0)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    registered = code == 0
    detail = "" if registered else (lines[0] if lines else f"schtasks 退出码 {code}")
    if not registered and TASK_FILE.exists():
        # Sandboxes and non-elevated accounts can make `schtasks /Query` fail on a
        # task that really exists; the definition file is the second witness.
        registered = True
        detail = "schtasks 查询失败，按计划任务定义文件判断为已注册"
    entry = _startup_entry()
    return {
        "task": WATCHDOG_TASK,
        "registered": registered,
        "detail": detail,
        "startup": bool(entry),
        "startup_entry": entry,
        "supported": os.name == "nt",
    }


def prerequisites() -> list[dict]:
    items = []
    for name, path, hint, optional in PREREQS:
        exists = Path(path).exists()
        items.append({
            "name": name, "path": str(path), "hint": hint, "optional": optional,
            "ok": exists, "state": "ok" if exists else ("optional" if optional else "missing"),
        })
    return items


def log_tail(limit: int = 24) -> dict:
    try:
        lines = LOG_FILE.read_text("utf-8", "replace").splitlines()
        stamp = _iso(datetime.fromtimestamp(LOG_FILE.stat().st_mtime))
    except OSError:
        return {"path": str(LOG_FILE), "exists": False, "lines": [], "updated_at": None}
    tail = [line for line in lines[-limit:] if line.strip()]
    return {"path": str(LOG_FILE), "exists": True, "lines": tail, "updated_at": stamp}


def snapshot() -> dict:
    services: list[dict] = []
    owners: set[int] = set()
    for key, name, port, hint in SERVICES:
        try:
            listening = _listening(port)
            connections = _port_state(port)[1]
            # onebot 看的是"有没有 QQ 真连上来"（ESTABLISHED 计数），不是端口在听。
            # 这一句以前在 try 外面：netstat 探不通（OSError）时会冒出去，
            # 让整个 /api/operations/services 面板 500。
            linked = bool(_established(port)) if key == "onebot" else listening
        except OSError:
            listening, connections, linked = False, 0, False
        running = linked if key == "onebot" else listening
        pid = _port_state(port)[2] if (listening or connections) else None
        if pid:
            owners.add(pid)
        state = "ready" if running else ("partial" if listening else "stopped")
        services.append({
            "id": key, "name": name, "port": port, "hint": hint,
            "running": running, "listening": listening,
            "connections": connections, "pid": pid, "process": "",
            "state": state,
        })
    if owners:
        names = _resolve_processes(owners)
        own = os.getpid()
        for item in services:
            pid = item["pid"]
            if not pid:
                continue
            item["process"] = "aiji 核心（本进程）" if pid == own else names.get(pid, "")
    blocked = next((item for item in services if not item["running"]), None)
    ready = sum(1 for item in services if item["running"])
    state = desired_state()
    return {
        "services": services,
        "order": [item["id"] for item in services],
        "ready": ready,
        "total": len(services),
        "all_ready": ready == len(services),
        "blocked": None if blocked is None else {
            "id": blocked["id"], "name": blocked["name"], "port": blocked["port"],
            "state": blocked["state"], "hint": FIX_HINTS.get(blocked["id"], ""),
        },
        "desired": {"state": state, "file": str(STATE_FILE)},
        "watchdog": watchdog(),
        "prerequisites": prerequisites(),
        "log": log_tail(),
        "checked_at": _iso(datetime.now()),
        "actions_available": os.name == "nt" and SCRIPT.exists(),
        "start_script": str(SCRIPT),
    }


def launch(action: str) -> None:
    if action not in ("start", "stop", "restart"):
        raise ValueError("未知服务操作")
    if os.name != "nt" or not SCRIPT.exists():
        raise RuntimeError("当前系统不支持页面启停服务")
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(SCRIPT), "-Action", action], cwd=SCRIPT.parents[1],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
    )


def _watchdog_on() -> dict:
    if os.name != "nt":
        raise RuntimeError("当前系统不支持计划任务")
    if not WATCHDOG_SCRIPT.exists():
        raise RuntimeError(f"缺少保活脚本：{WATCHDOG_SCRIPT}")
    command = (f'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
               f'-File "{WATCHDOG_SCRIPT}"')
    code, text = _run(["schtasks", "/Create", "/TN", WATCHDOG_TASK, "/TR", command,
                       "/SC", "MINUTE", "/MO", "5", "/F"], timeout=25.0)
    if code != 0:
        raise RuntimeError((text.strip() or f"schtasks 退出码 {code}")[:300])
    return {"action": "watchdog-on", "detail": "每 5 分钟保活任务已注册"}


def _watchdog_off() -> dict:
    if os.name != "nt":
        raise RuntimeError("当前系统不支持计划任务")
    if not watchdog()["registered"]:
        return {"action": "watchdog-off", "detail": "保活任务本来就不存在"}
    code, text = _run(["schtasks", "/Delete", "/TN", WATCHDOG_TASK, "/F"], timeout=20.0)
    if code != 0:
        raise RuntimeError((text.strip() or f"schtasks 退出码 {code}")[:300])
    return {"action": "watchdog-off", "detail": "保活任务已注销"}


def control(action: str) -> dict:
    """Apply one whitelisted lifecycle action."""
    action = str(action or "")
    if action in ("start", "stop", "restart"):
        launch(action)
        return {"action": action, "detail": f"已下发 {action}"}
    if action == "watchdog-on":
        return _watchdog_on()
    if action == "watchdog-off":
        return _watchdog_off()
    raise ValueError("未知服务操作")
