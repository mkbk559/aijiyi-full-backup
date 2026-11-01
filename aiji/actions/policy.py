"""执行策略：工作区边界、敏感文件与危险命令黑名单。"""

from __future__ import annotations

import re
from pathlib import Path


class PolicyViolation(RuntimeError):
    pass


_SENSITIVE_NAMES = {
    ".env", ".env.local", ".git-credentials", "credentials.yaml",
    ".credentials.yaml", "id_rsa", "id_ed25519",
}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}
_DANGEROUS = (
    (re.compile(r"\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)\b", re.I), "dangerous recursive delete"),
    (re.compile(r"\b(?:del|erase)\s+/[fsq]\b", re.I), "dangerous Windows delete"),
    (re.compile(r"\b(?:format|diskpart|mkfs(?:\.\w+)?)\b", re.I), "disk formatting"),
    (re.compile(r"\b(?:shutdown|reboot)\b", re.I), "system shutdown"),
    (re.compile(r"\breg(?:\.exe)?\s+delete\b", re.I), "registry deletion"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), "destructive git reset"),
    (re.compile(r"\bgit\s+clean\s+-[a-z]*f", re.I), "destructive git clean"),
    (re.compile(r"\bgit\s+checkout\s+--\s+", re.I), "destructive git checkout"),
    (re.compile(r"\bRemove-Item\b.*\b-Recurse\b", re.I), "recursive PowerShell delete"),
    (re.compile(r"(?:curl|wget|iwr|Invoke-WebRequest).*\|\s*(?:sh|bash|pwsh|powershell)", re.I),
     "remote script execution"),
    (re.compile(r"\b(?:Invoke-Expression|iex)\b", re.I), "dynamic code execution"),
)


class ActionPolicy:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def resolve(self, raw: str = ".") -> Path:
        path = Path(raw or ".")
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise PolicyViolation(f"path outside workspace: {raw}") from exc
        return resolved

    def check_file(self, path: Path, *, write: bool = False) -> None:
        rel = path.relative_to(self.root)
        parts = {part.lower() for part in rel.parts}
        if ".git" in parts:
            raise PolicyViolation("access to .git is blocked")
        name = path.name.lower()
        if name in _SENSITIVE_NAMES or path.suffix.lower() in _SENSITIVE_SUFFIXES:
            raise PolicyViolation(f"sensitive file blocked: {rel}")
        if write and path.exists() and path.is_dir():
            raise PolicyViolation("cannot write to a directory")

    def check_command(self, command: str) -> tuple[bool, str]:
        cmd = (command or "").strip()
        if not cmd:
            return False, "empty command"
        if "\x00" in cmd:
            return False, "NUL byte blocked"
        for pattern, reason in _DANGEROUS:
            if pattern.search(cmd):
                return False, f"dangerous command blocked: {reason}"
        return True, ""


__all__ = ["ActionPolicy", "PolicyViolation"]
