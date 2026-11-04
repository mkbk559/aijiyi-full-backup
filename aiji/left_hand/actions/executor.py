"""文件、终端和桌面工具的执行器。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
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
            if call.name in ("web_search", "fetch_url", "bili_search"):
                return self._net_readonly(call)
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

    # ---- 联网只读工具（web_search / fetch_url / bili_search）----
    # 设计：全只读、免审批（risk=read）；fetch_url 走 net_guard 公网校验；
    # 死代理（Clash 没开但环境变量在）自动绕开——否则所有请求被塞进拒绝连接的端口。
    def _net_opener(self):
        import urllib.request

        proxy = (os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip()
        if proxy:
            import socket as _socket
            import urllib.parse as _up

            url = proxy if "://" in proxy else f"http://{proxy}"
            host = _up.urlparse(url).hostname or "127.0.0.1"
            port = _up.urlparse(url).port or 7897
            try:
                with _socket.create_connection((host, port), timeout=0.8):
                    return urllib.request.build_opener()  # 代理活着：用系统配置
            except OSError:
                pass  # 死代理：直连
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _net_readonly(self, call: ToolCall) -> ToolResult:
        if call.name == "web_search":
            return self._web_search(call)
        if call.name == "fetch_url":
            return self._fetch_url(call)
        return self._bili_search(call)

    def _http_get(self, url: str, *, timeout_s: float = 15.0,
                  headers: dict | None = None) -> str:
        from aiji.throat.net_guard import validate_public_http_url

        validate_public_http_url(url)
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (AIJiYi readonly tools)",
                          **(headers or {})})
        with self._net_opener().open(req, timeout=timeout_s) as resp:
            data = resp.read(400_000)
        return data.decode("utf-8", "replace")

    _BROWSER_HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/131.0.0.0 Safari/537.36"),
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    def _web_search(self, call: ToolCall) -> ToolResult:
        query = str(call.arguments.get("query") or "").strip()
        limit = max(1, min(10, int(call.arguments.get("limit", 6))))
        if not query:
            return ToolResult(call.id, call.name, False, error="query required")
        import urllib.parse

        # Bing 国际版：免 key、墙内直连稳（DDG html 版实测超时；Baidu 结构冗杂）。
        url = ("https://www.bing.com/search?q="
               + urllib.parse.quote(query) + "&setmkt=zh-CN")
        html = self._http_get(url, timeout_s=20.0, headers=self._BROWSER_HEADERS)
        results = []
        for match in re.finditer(
            r'<h2><a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a></h2>', html,
        ):
            href = match.group(1)
            title = re.sub(r"<[^>]+>", "", match.group(2))[:120]
            if title and "bing.com" not in href:
                results.append({"title": title, "url": href})
            if len(results) >= limit:
                break
        output = "\n".join(
            f"{i}. {r['title']}\n   {r['url']}" for i, r in enumerate(results, 1))
        if not results:
            return ToolResult(call.id, call.name, False,
                              error="no results (engine blocked or empty page)")
        return ToolResult(call.id, call.name, True, output=output)

    def _fetch_url(self, call: ToolCall) -> ToolResult:
        url = str(call.arguments.get("url") or "").strip()
        max_chars = max(500, min(20000, int(call.arguments.get("max_chars", 6000))))
        if not url:
            return ToolResult(call.id, call.name, False, error="url required")
        html = self._http_get(url, timeout_s=20.0)
        text = self._html_to_text(html)[:max_chars]
        return ToolResult(call.id, call.name, True, output=text or "(empty page)")

    @staticmethod
    def _html_to_text(html: str) -> str:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                      flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
        return re.sub(r"\s{2,}", " ", text).strip()

    def _bili_search(self, call: ToolCall) -> ToolResult:
        keyword = str(call.arguments.get("keyword") or "").strip()
        limit = max(1, min(10, int(call.arguments.get("limit", 6))))
        if not keyword:
            return ToolResult(call.id, call.name, False, error="keyword required")
        import urllib.parse

        # B 站公开 web 搜索接口（无需登录态；SESSDATA 只在发评论/投稿时才要）。
        # 412 = 反爬：必须带完整浏览器头（含 Referer + buvid cookie 占位）。
        url = ("https://api.bilibili.com/x/web-interface/search/type?"
               + urllib.parse.urlencode(
                   {"search_type": "video", "keyword": keyword, "page": 1}))
        raw = self._http_get(
            url, timeout_s=20.0,
            headers={**self._BROWSER_HEADERS,
                     "Referer": "https://www.bilibili.com/",
                     "Cookie": "buvid3=aiji-readonly-tool"})
        data = json.loads(raw)
        results = []
        for item in (data.get("data") or {}).get("result") or []:
            title = re.sub(r"<[^>]+>", "", str(item.get("title") or ""))[:80]
            author = str(item.get("author") or "")
            bvid = str(item.get("bvid") or "")
            if title and bvid:
                results.append(
                    {"title": title, "author": author,
                     "url": f"https://www.bilibili.com/video/{bvid}/"})
            if len(results) >= limit:
                break
        output = "\n".join(
            f"{i}. {r['title']}（{r['author']}）\n   {r['url']}"
            for i, r in enumerate(results, 1))
        if not results:
            return ToolResult(call.id, call.name, False,
                              error="no results (rate-limited or empty)")
        return ToolResult(call.id, call.name, True, output=output)


__all__ = ["ActionExecutor", "ToolResult"]
