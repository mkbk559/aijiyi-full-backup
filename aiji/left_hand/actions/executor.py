"""文件、终端和桌面工具的执行器。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from aiji.left_hand.actions.approval import ApprovalManager
from aiji.left_hand.actions.policy import ActionPolicy, PolicyViolation
from aiji.left_hand.actions.schema import ToolCall


@dataclass(slots=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    output: str = ""
    error: str = ""
    approved: bool = True
    risk: str = "read"


class ActionExecutor:
    def __init__(
        self,
        root: str | Path,
        *,
        approvals: ApprovalManager | None = None,
        approval_timeout_s: float = 120.0,
    ):
        self.policy = ActionPolicy(root)
        self.root = self.policy.root
        self.approvals = approvals or ApprovalManager()
        self.approval_timeout_s = float(approval_timeout_s)

    def execute(self, call: ToolCall) -> ToolResult:
        try:
            if call.name == "read_file":
                return self._read_file(call)
            if call.name == "list_files":
                return self._list_files(call)
            if call.name == "write_file":
                return self._write_file(call)
            if call.name == "execute_terminal":
                return self._terminal(call)
            if call.name in ("screenshot", "mouse_click", "type_text", "hotkey"):
                return self._desktop(call)
            return ToolResult(call.id, call.name, False, error="unknown tool", risk="unknown")
        except PolicyViolation as exc:
            return ToolResult(call.id, call.name, False, error=str(exc), approved=False, risk="blocked")
        except Exception as exc:  # noqa: BLE001 - 工具失败返回模型，不拖垮主链路
            return ToolResult(call.id, call.name, False,
                              error=f"{type(exc).__name__}: {exc}", risk="error")

    def _read_file(self, call: ToolCall) -> ToolResult:
        path = self.policy.resolve(str(call.arguments.get("path") or ""))
        self.policy.check_file(path)
        if not path.is_file():
            return ToolResult(call.id, call.name, False, error="file not found")
        data = path.read_bytes()[:200_000]
        return ToolResult(call.id, call.name, True, data.decode("utf-8", "replace"))

    def _list_files(self, call: ToolCall) -> ToolResult:
        path = self.policy.resolve(str(call.arguments.get("path") or "."))
        self.policy.check_file(path)
        if not path.is_dir():
            return ToolResult(call.id, call.name, False, error="directory not found")
        recursive = bool(call.arguments.get("recursive", False))
        limit = max(1, min(1000, int(call.arguments.get("max_entries", 200))))
        items = path.rglob("*") if recursive else path.iterdir()
        rows = []
        for item in items:
            try:
                rel = item.relative_to(self.root).as_posix()
                if ".git" in item.relative_to(self.root).parts:
                    continue
                rows.append({"path": rel, "type": "dir" if item.is_dir() else "file"})
            except (ValueError, OSError):
                continue
            if len(rows) >= limit:
                break
        return ToolResult(call.id, call.name, True,
                          json.dumps(rows, ensure_ascii=False, indent=2))

    def _write_file(self, call: ToolCall) -> ToolResult:
        path = self.policy.resolve(str(call.arguments.get("path") or ""))
        self.policy.check_file(path, write=True)
        content = str(call.arguments.get("content") or "")
        approved = self.approvals.request(
            kind="write_file",
            summary=f"修改 {path.relative_to(self.root).as_posix()} ({len(content)} 字符)",
            arguments={"path": path.relative_to(self.root).as_posix(), "content": content},
            timeout_s=self.approval_timeout_s,
        )
        if not approved:
            return ToolResult(call.id, call.name, False, error="user rejected",
                              approved=False, risk="write")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(call.id, call.name, True,
                          f"wrote {path.relative_to(self.root).as_posix()}",
                          risk="write")

    def _terminal(self, call: ToolCall) -> ToolResult:
        command = str(call.arguments.get("command") or "").strip()
        ok, reason = self.policy.check_command(command)
        if not ok:
            return ToolResult(call.id, call.name, False, error=reason,
                              approved=False, risk="blocked")
        cwd = self.policy.resolve(str(call.arguments.get("cwd") or "."))
        if not cwd.is_dir():
            return ToolResult(call.id, call.name, False, error="cwd not found", risk="terminal")
        approved = self.approvals.request(
            kind="execute_terminal",
            summary=f"执行命令：{command}",
            arguments={"command": command,
                       "cwd": cwd.relative_to(self.root).as_posix()},
            timeout_s=self.approval_timeout_s,
        )
        if not approved:
            return ToolResult(call.id, call.name, False, error="user rejected",
                              approved=False, risk="terminal")
        timeout = max(1, min(120, int(call.arguments.get("timeout_s", 60))))
        if sys.platform == "win32":
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            argv = ["bash", "-lc", command]
        env = {
            key: value for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        }
        proc = subprocess.run(
            argv, cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        output = ((proc.stdout or "") + (proc.stderr or ""))[-30_000:]
        return ToolResult(call.id, call.name, proc.returncode == 0,
                          output=output, error="" if proc.returncode == 0 else f"exit {proc.returncode}",
                          risk="terminal")

    def _desktop(self, call: ToolCall) -> ToolResult:
        approved = self.approvals.request(
            kind=call.name,
            summary=f"桌面操作：{call.name}",
            arguments=dict(call.arguments),
            timeout_s=self.approval_timeout_s,
        )
        if not approved:
            return ToolResult(call.id, call.name, False, error="user rejected",
                              approved=False, risk="desktop")
        from aiji.left_hand.actions.gui import execute_desktop
        output = execute_desktop(call.name, call.arguments)
        return ToolResult(call.id, call.name, True, output=output, risk="desktop")


__all__ = ["ActionExecutor", "ToolResult"]
