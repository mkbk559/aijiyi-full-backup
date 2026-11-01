"""把桌宠对话接到 DSH（DeepSeek Harness），让大肥鱼能用 dsh 的全套能力。

实现方式：调 `dsh headless --json` 子进程。
- `--json` 出 NDJSON：`session` / `status` / `final`（结构已实测）
- `--session-id` 接续同一个 Session，所以能连续对话、保留上下文
- 每次任务都是一个完整 agent：能读文件、跑命令、用 MCP/插件，即"dsh 的一切功能"

凭证：headless profile 的 provider 走环境变量（如 WB_PROXY_API_KEY），
而桌面版把密钥存在 `~/.dsh/.credentials.yaml` 的 `refs` 段。这里把它读出来注入子进程环境，
**不落盘、不打印**，只在内存里传给子进程。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_DSH = r"D:\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd"
DEFAULT_CREDENTIALS = Path(os.path.expanduser("~")) / ".dsh" / ".credentials.yaml"


def load_credential_refs(path: str | Path = DEFAULT_CREDENTIALS) -> dict[str, str]:
    """从 .credentials.yaml 的 refs 段读出 ENV_NAME -> secret。

    只做最小解析（不引入 yaml 依赖）：refs 下是 `  NAME: value` 两空格缩进的键值。
    """
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return out
    in_refs = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if not line.startswith(" ") and stripped.endswith(":"):
            in_refs = stripped[:-1] == "refs"
            continue
        if in_refs and ":" in stripped:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip().strip("'\"")
            if key and val:
                out[key] = val
    return out


class DshBridge:
    """一次提问 = 一次 `dsh headless` 运行；session 自动续接。"""

    def __init__(self, cwd: str | Path = ".", *, dsh: str = DEFAULT_DSH,
                 timeout_s: float = 300.0,
                 credentials: str | Path = DEFAULT_CREDENTIALS,
                 session_id: str = "", stderr_dir: str | Path = ".tmp"):
        self.cwd = str(Path(cwd).resolve())
        self.dsh = dsh
        self.timeout_s = timeout_s
        self.credentials = credentials
        self.session_id = session_id
        self.stderr_dir = Path(stderr_dir)
        self._lock = threading.Lock()

    # ---- 内部 ----

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        for k, v in load_credential_refs(self.credentials).items():
            env.setdefault(k, v)
        return env

    def _cmd(self, text: str) -> list[str]:
        cmd = [self.dsh, "headless", "--json"]
        if self.session_id:
            cmd += ["--session-id", self.session_id]
        cmd.append(text)
        return cmd

    # ---- 对外 ----

    def ask(self, text: str, *, on_event: Callable[[str], None] | None = None
            ) -> dict[str, Any]:
        """问一句，等它答完。返回 {ok, text, session_id, error, took_ms, tools}。"""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "text": "", "error": "empty", "session_id": self.session_id,
                    "took_ms": 0.0, "tools": []}

        t0 = time.time()
        err = ""
        final = ""
        tools: list[str] = []
        session = self.session_id
        with self._lock:
            # stderr 必须重定向到文件：dsh 会往 stderr 写大量诊断，
            # 若用 PIPE 而不读，管道塞满后子进程会卡死（本轮踩过，表现为"永远不返回"）。
            stderr_path = Path(self.stderr_dir) / f"dsh_stderr_{os.getpid()}_{int(t0)}.log"
            try:
                stderr_path.parent.mkdir(parents=True, exist_ok=True)
                stderr_fh = stderr_path.open("w+", encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                stderr_fh = None

            try:
                proc = subprocess.Popen(
                    self._cmd(text), cwd=self.cwd, env=self._env(),
                    stdout=subprocess.PIPE,
                    stderr=stderr_fh if stderr_fh is not None else subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
            except Exception as e:  # noqa: BLE001
                if stderr_fh is not None:
                    stderr_fh.close()
                return {"ok": False, "text": "", "error": f"{type(e).__name__}: {e}",
                        "session_id": session, "took_ms": 0.0, "tools": []}

            # 看门狗：到点直接杀，避免界面永远"在想…"
            timer = threading.Timer(self.timeout_s, lambda: proc.poll() is None and proc.kill())
            timer.daemon = True
            timer.start()

            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    kind = ev.get("type")
                    if kind == "session":
                        session = ev.get("sessionId") or session
                        if on_event:
                            on_event("会话已建立")
                    elif kind == "status":
                        phase = ev.get("phase")
                        if phase == "step_start" and on_event:
                            on_event("在干活…（可调用工具）")
                        elif phase == "turn_end":
                            reason = ev.get("reason") or {}
                            if reason.get("kind") == "error":
                                err = (reason.get("error") or {}).get("message", "error")
                    elif kind == "tool":
                        name = ev.get("name") or ev.get("tool") or ""
                        if name:
                            tools.append(str(name))
                            if on_event:
                                on_event(f"用工具：{name}")
                    elif kind == "final":
                        final = ev.get("text") or ""
                proc.wait(timeout=10)
            except Exception as e:  # noqa: BLE001
                err = err or f"{type(e).__name__}: {e}"
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                timer.cancel()

            if stderr_fh is not None:
                try:
                    stderr_fh.flush()
                    stderr_fh.seek(0)
                    tail = [x for x in stderr_fh.read().strip().splitlines() if x.strip()]
                    if not err and proc.returncode not in (0, None) and tail:
                        err = tail[-1][:400]
                    elif err and tail:
                        err = f"{err} | {tail[-1][:200]}"
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    stderr_fh.close()
                try:
                    stderr_path.unlink()
                except Exception:  # noqa: BLE001
                    pass

        self.session_id = session
        ok = bool(final) and not err
        return {"ok": ok, "text": final.strip(), "error": err, "session_id": session,
                "took_ms": round((time.time() - t0) * 1000, 1), "tools": tools}


__all__ = ["DshBridge", "DEFAULT_CREDENTIALS", "DEFAULT_DSH", "load_credential_refs"]
