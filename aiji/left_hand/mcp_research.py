"""Controlled GitHub discovery for MCP servers.

Discovery never installs or executes a repository it finds. It searches GitHub,
reads public README files through an allowlisted read-only MCP fetch server,
classifies risk, and stores candidates for review.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiji.left_hand.mcp_client import MCPRegistry
from aiji import net

logger = logging.getLogger(__name__)


SAFE_LICENSES = frozenset((
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0",
))
READ_ONLY_TERMS = (
    "read-only", "read only", "search", "fetch", "query", "list", "read",
    "documentation", "docs", "discover", "lookup", "inspect",
)
RISK_TERMS = (
    "execute", "terminal", "shell", "delete file", "write file", "write",
    "create", "update", "publish", "post", "send", "deploy", "trade", "payment",
    "wallet", "credential", "browser automation",
)
STRONG_RISK_TERMS = (
    "execute", "terminal", "shell", "delete file", "deploy", "trade",
    "payment", "wallet", "credential", "browser automation",
)
BLOCKED_REPO_TERMS = (
    "malware", "ransomware", "credential stealer", "exploit kit",
)
QUERIES = (
    'duckduckgo mcp server in:name,description stars:>5 archived:false',
    'mcp-server-fetch in:name,description stars:>5 archived:false',
    'topic:mcp-server "server" in:name stars:>20 archived:false',
    'mcp server in:name,description stars:>20 archived:false',
)
_INSTALL_UVX = re.compile(r"\buvx\s+([A-Za-z0-9_.-]+)")
_INSTALL_NPX = re.compile(r"\bnpx(?:\s+-y)?\s+(@?[A-Za-z0-9_./@-]+)")


def _term_hits(terms: tuple[str, ...], text: str) -> list[str]:
    return [
        term for term in terms
        if re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text
        )
    ]


def classify_readme(text: str) -> dict[str, Any]:
    raw = str(text or "").lower()
    lines = raw.splitlines()
    selected: list[str] = []
    in_tools = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and "tool" in stripped:
            in_tools = True
            selected.append(stripped)
            continue
        if in_tools and stripped.startswith("## ") and "tool" not in stripped:
            break
        if in_tools:
            selected.append(stripped)
    value = " ".join(selected) if selected else raw
    value = " ".join(value.split())
    canonical = re.sub(r"[_/]+", " ", value)
    read_hits = _term_hits(READ_ONLY_TERMS, canonical)
    risk_hits = _term_hits(RISK_TERMS, canonical)
    strong_risk_hits = _term_hits(STRONG_RISK_TERMS, canonical)
    if _term_hits(BLOCKED_REPO_TERMS, canonical) or strong_risk_hits:
        risk = "blocked"
    elif risk_hits:
        risk = "review_required"
    elif read_hits:
        risk = "read_only"
    else:
        risk = "review_required"
    return {
        "risk": risk,
        "read_only_capabilities": read_hits[:12],
        "risk_capabilities": risk_hits[:12],
    }


def extract_install_hint(text: str) -> str:
    value = str(text or "")
    match = _INSTALL_UVX.search(value)
    if match:
        return f"uvx {match.group(1)}"
    match = _INSTALL_NPX.search(value)
    if match:
        return f"npx -y {match.group(1)}"
    if "docker run" in value.lower():
        return "docker run (review required)"
    return ""


def windows_compatible(text: str) -> bool:
    value = " ".join(str(text or "").lower().split())
    blocked = (
        "windows is not supported",
        "does not support windows",
        "linux and macos only",
        "supported os: linux and macos",
    )
    return not any(term in value for term in blocked)


def _safe_github_raw(repo: str, branch: str) -> str:
    clean_repo = str(repo or "").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", clean_repo):
        raise ValueError("invalid GitHub repository name")
    clean_branch = urllib.parse.quote(str(branch or "main"), safe="._-/")
    return f"https://raw.githubusercontent.com/{clean_repo}/{clean_branch}/README.md"


class GitHubClient:
    def __init__(
        self,
        *,
        token: str = "",
        timeout_s: float = 20.0,
    ) -> None:
        self.token = token or os.environ.get(
            "GITHUB_TOKEN", os.environ.get("GH_TOKEN", ""))
        self.timeout_s = max(5.0, float(timeout_s))

    def search_repositories(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        encoded = urllib.parse.urlencode({
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": max(1, min(50, int(limit))),
        })
        payload = self._get_json(
            f"https://api.github.com/search/repositories?{encoded}")
        items = payload.get("items") if isinstance(payload, dict) else None
        return [item for item in (items or []) if isinstance(item, dict)]

    def _get_json(self, url: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AIJiYi-MCP-Research",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with net.urlopen(
                request, timeout=self.timeout_s,
            ) as response:
                body = response.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"GitHub request failed: {exc}") from exc
        value = json.loads(body)
        if not isinstance(value, dict):
            raise TypeError("GitHub returned a non-object response")
        return value


class MCPResearcher:
    def __init__(
        self,
        *,
        output_dir: str | Path,
        github: GitHubClient | None = None,
        registry: MCPRegistry | None = None,
        fetch_text: Callable[[str], str] | None = None,
        observer: Callable[[str], Any] | None = None,
        max_candidates: int = 10,
        max_fetches: int = 10,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.reports_dir = self.output_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.github = github or GitHubClient()
        self.registry = registry
        self.fetch_text = fetch_text or self._fetch_via_mcp
        self.observer = observer
        self.max_candidates = max(1, min(30, int(max_candidates)))
        self.max_fetches = max(1, min(12, int(max_fetches)))
        self._lock = threading.Lock()

    def _fetch_via_mcp(self, url: str) -> str:
        if self.registry is None:
            raise RuntimeError("MCP registry is not configured")
        result = self.registry.call_sync(
            "fetch",
            "fetch",
            {
                "url": url,
                "max_length": 18_000,
                "start_index": 0,
                "raw": False,
            },
            timeout_s=90,
        )
        if result.is_error:
            raise RuntimeError(result.text[:500])
        return result.text

    def run_once(self, *, now: float | None = None) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("MCP research is already running")
        try:
            current = time.time() if now is None else float(now)
            cutoff = (
                datetime.now(UTC) - timedelta(days=365)
            ).strftime("%Y-%m-%d")
            queries = tuple(
                query if "pushed:" in query else f"{query} pushed:>{cutoff}"
                for query in QUERIES
            )
            discovered: dict[str, dict[str, Any]] = {}
            for query_index, query in enumerate(queries):
                try:
                    rows = self.github.search_repositories(query, limit=20)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GitHub MCP query failed: %s", exc)
                    continue
                for row in rows:
                    name = str(row.get("full_name") or "").strip()
                    if name and name not in discovered:
                        item = dict(row)
                        item["_query_boost"] = (
                            len(queries) - query_index) * 2.5
                        discovered[name] = item
            ranked = sorted(
                discovered.values(),
                key=lambda row: (
                    self._pre_score(row)
                    + float(row.get("_query_boost") or 0.0)
                ),
                reverse=True,
            )
            fetched = 0
            candidates: list[dict[str, Any]] = []
            for row in ranked:
                if fetched >= self.max_fetches:
                    break
                repo = str(row.get("full_name") or "")
                branch = str(row.get("default_branch") or "main")
                try:
                    readme_url = _safe_github_raw(repo, branch)
                    readme = self.fetch_text(readme_url)
                    fetched += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("MCP README fetch failed for %s: %s", repo, exc)
                    readme = ""
                candidate = self._candidate(row, readme)
                candidates.append(candidate)
            candidates.sort(
                key=lambda item: (
                    not bool(item["eligible"]),
                    -float(item["score"]),
                    item["full_name"],
                )
            )
            candidates = candidates[:self.max_candidates]
            report = {
                "version": 1,
                "created_at": current,
                "queries": list(queries),
                "discovered": len(discovered),
                "fetched_readmes": fetched,
                "eligible": sum(1 for item in candidates if item["eligible"]),
                "candidates": candidates,
            }
            summary = self._summary(report)
            report["summary"] = summary
            self._save(report)
            if self.observer is not None:
                self.observer(summary)
            return report
        finally:
            self._lock.release()

    def snapshot(self) -> dict[str, Any]:
        path = self.output_dir / "latest.json"
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"available": False, "latest": None}
        return {"available": True, "latest": value}

    def _candidate(self, row: dict[str, Any], readme: str) -> dict[str, Any]:
        license_info = row.get("license") if isinstance(row.get("license"), dict) else {}
        license_id = str(license_info.get("spdx_id") or "")
        classification = classify_readme(readme)
        install_hint = extract_install_hint(readme)
        platform_compatible = windows_compatible(readme)
        direct_install = (
            install_hint.startswith(("uvx ", "npx -y "))
            and "@smithery/cli" not in install_hint
            and "docker run" not in install_hint
        )
        eligible = bool(
            not row.get("archived")
            and classification["risk"] == "read_only"
            and license_id in SAFE_LICENSES
            and direct_install
            and platform_compatible
        )
        score = self._score(row, classification, install_hint)
        return {
            "full_name": str(row.get("full_name") or ""),
            "url": str(row.get("html_url") or ""),
            "description": str(row.get("description") or "")[:1000],
            "stars": int(row.get("stargazers_count") or 0),
            "updated_at": str(row.get("updated_at") or ""),
            "license": license_id,
            "topics": [str(item) for item in (row.get("topics") or [])][:30],
            "risk": classification["risk"],
            "read_only_capabilities": classification["read_only_capabilities"],
            "risk_capabilities": classification["risk_capabilities"],
            "install_hint": install_hint,
            "platform_compatible": platform_compatible,
            "direct_install": direct_install,
            "eligible": eligible,
            "auto_install_allowed": False,
            "readme_excerpt": " ".join(readme.split())[:1800],
            "score": round(score, 3),
        }

    @staticmethod
    def _pre_score(row: dict[str, Any]) -> float:
        text = " ".join((
            str(row.get("full_name") or ""),
            str(row.get("description") or ""),
            " ".join(str(item) for item in (row.get("topics") or [])),
        )).lower()
        if "mcp" not in text or "server" not in text:
            return -1000.0
        if any(term in text for term in BLOCKED_REPO_TERMS):
            return -900.0
        score = min(
            4.0,
            math.log10(max(1, int(row.get("stargazers_count") or 0))),
        )
        read_hits = sum(1 for term in READ_ONLY_TERMS if term in text)
        score += min(3, read_hits) * 2.5
        if any(term in text for term in STRONG_RISK_TERMS):
            score -= 8.0
        return score

    @staticmethod
    def _score(
        row: dict[str, Any],
        classification: dict[str, Any],
        install_hint: str,
    ) -> float:
        stars = max(1, int(row.get("stargazers_count") or 0))
        score = math.log10(stars) * 5.0
        if classification["risk"] == "read_only":
            score += 3.0
        elif classification["risk"] == "blocked":
            score -= 8.0
        if install_hint:
            score += 2.0
        if str((row.get("license") or {}).get("spdx_id") or "") in SAFE_LICENSES:
            score += 1.0
        return score

    def _save(self, report: dict[str, Any]) -> None:
        latest = self.output_dir / "latest.json"
        temp = latest.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, latest)
        report_path = self.reports_dir / (
            time.strftime("%Y%m%d-%H%M%S", time.localtime(report["created_at"]))
            + f"-{uuid.uuid4().hex[:6]}.json"
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _summary(report: dict[str, Any]) -> str:
        eligible = [
            item for item in report["candidates"] if item["eligible"]
        ][:3]
        names = "、".join(item["full_name"] for item in eligible) or "暂无"
        return (
            f"去 GitHub 翻了 MCP：检查 {report['discovered']} 个仓库，"
            f"读了 {report['fetched_readmes']} 份 README，"
            f"筛出 {report['eligible']} 个只读候选。"
            f"优先看：{names}。只记录，不会自动安装或执行。"
        )


class MCPResearchLoop:
    def __init__(
        self,
        researcher: MCPResearcher,
        *,
        interval_s: float = 6 * 3600,
        initial_delay_s: float = 90.0,
    ) -> None:
        self.researcher = researcher
        self.interval_s = max(300.0, float(interval_s))
        self.initial_delay_s = max(5.0, float(initial_delay_s))
        self.stats = {
            "runs": 0,
            "errors": 0,
            "last_run_at": 0.0,
            "last_error": "",
            "last_report": None,
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._work_lock = threading.Lock()

    def start(self) -> MCPResearchLoop:
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="aiji-mcp-research", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None

    def trigger(self) -> bool:
        if not self._work_lock.acquire(blocking=False):
            return False
        threading.Thread(
            target=self._work, name="aiji-mcp-research-manual", daemon=True
        ).start()
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            **dict(self.stats),
            "running": bool(self._thread and self._thread.is_alive()),
            "working": self._work_lock.locked(),
            "interval_s": self.interval_s,
            "initial_delay_s": self.initial_delay_s,
            "latest": self.researcher.snapshot(),
        }

    def _work(self) -> None:
        try:
            report = self.researcher.run_once()
            self.stats["last_report"] = {
                key: report[key]
                for key in ("created_at", "discovered", "eligible", "summary")
            }
            self.stats["runs"] += 1
            self.stats["last_run_at"] = time.time()
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            self._work_lock.release()

    def trigger_and_wait(self, timeout_s: float = 300.0) -> dict[str, Any]:
        if not self.trigger():
            raise RuntimeError("MCP research is already running")
        deadline = time.time() + max(5.0, timeout_s)
        while self._work_lock.locked() and time.time() < deadline:
            time.sleep(0.2)
        if self._work_lock.locked():
            raise RuntimeError("MCP research timed out")
        if self.stats["last_error"]:
            raise RuntimeError(self.stats["last_error"])
        return self.snapshot()

    def due_delay(self, *, now: float | None = None) -> float:
        latest = self.researcher.snapshot().get("latest") or {}
        last = float(latest.get("created_at") or 0.0)
        if not last:
            return 0.0
        current = time.time() if now is None else now
        return max(0.0, last + self.interval_s - current)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(self.due_delay()):
                return
            try:
                self.trigger_and_wait(timeout_s=300.0)
            except Exception as exc:  # noqa: BLE001
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(exc).__name__}: {exc}"
                if self._stop.wait(self.interval_s):
                    return


__all__ = [
    "GitHubClient",
    "MCPResearchLoop",
    "MCPResearcher",
    "classify_readme",
    "extract_install_hint",
    "windows_compatible",
]
