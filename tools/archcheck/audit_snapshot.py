"""审计快照：人格切换后果 / 配置落盘 / 旧身份残留 / 数据面 / 仓库卫生。

只读，不改任何东西。输出 JSON，供 evidence 文档引用。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "aiji.db"
OUT: dict = {}


def q(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error as exc:
        return [("ERR", str(exc))]


def main() -> None:
    conn = sqlite3.connect(str(DB))

    # ---- A. 按 persona 分组的数据（切默认人格后谁变成了孤儿） ----
    tables = [r[0] for r in q(conn, "select name from sqlite_master where type='table'")]
    persona_tables = {}
    for table in tables:
        cols = [r[1] for r in q(conn, f"pragma table_info({table})")]
        if "persona" in cols:
            rows = q(conn, f"select persona, count(*) from {table} group by persona")
            persona_tables[table] = {str(k): v for k, v in rows}
    OUT["persona_rows"] = persona_tables

    # ---- B. 学习语料 / 风格数据按 persona 分 ----
    for table in ("style", "trait"):
        cols = [r[1] for r in q(conn, f"pragma table_info({table})")]
        if "kind" in cols:
            OUT[f"{table}_kinds"] = {
                str(k): v for k, v in q(conn, f"select kind, count(*) from {table} group by kind")
            }
    OUT["trait_rows"] = q(conn, "select count(*) from trait")
    OUT["style_rows"] = q(conn, "select count(*) from style")

    # ---- C. 数据面 ----
    OUT["media"] = {
        str(k): v for k, v in q(conn, "select status, count(*) from media group by status")
    }
    OUT["media_recent"] = q(
        conn, "select status, count(*) from media where created_at > ? group by status",
        (1_760_000_000,),
    )
    OUT["turns"] = q(conn, "select count(*) from turn")
    OUT["facts"] = q(conn, "select count(*) from fact")

    # ---- D. 旧身份残留：docs / data / config ----
    leftover: dict[str, list[str]] = {}
    for base in ("docs", "data", "config", "aiji"):
        for path in (ROOT / base).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".html", ".txt"}:
                continue
            if "_backup" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits = [w for w in ("大肥鱼", "住在电脑", "一条鱼", "鱼缸", "本鱼")
                    if w in text]
            if hits:
                leftover[str(path.relative_to(ROOT))] = hits
    OUT["legacy_identity_files"] = dict(sorted(leftover.items())[:40])
    OUT["legacy_identity_count"] = len(leftover)

    # ---- E. 仓库卫生 ----
    stray = []
    for path in ROOT.iterdir():
        if path.is_dir() and path.name not in {
            ".git", ".github", ".hypothesis", ".ruff_cache", ".tmp", ".venv",
            "aiji", "bench", "build", "config", "data", "dist", "docs", "logs",
            "tests", "tools", "torchinductor_22166", "voice-engine", "aiji.egg-info",
        }:
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            stray.append({"path": path.name, "mb": round(size / 1e6, 2)})
    OUT["stray_dirs"] = stray
    OUT["db_mb"] = round(DB.stat().st_size / 1e6, 1)
    for name in ("build", "dist", "torchinductor_22166", "aiji.egg-info"):
        target = ROOT / name
        if target.exists():
            size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
            OUT.setdefault("build_artifacts", {})[name] = round(size / 1e6, 2)

    # ---- F. 配置落盘路径 ----
    sys.path.insert(0, str(ROOT))
    from aiji.right_hand import operations  # noqa: E402

    cfg = Path(operations.CONFIG_PATH)
    OUT["config"] = {
        "CONFIG_PATH": str(cfg),
        "abs": str(cfg if cfg.is_absolute() else (ROOT / cfg)),
        "exists": cfg.exists(),
        "parent_exists": cfg.parent.exists(),
    }

    # ---- G. 声音档案（还是鱼的命名） ----
    voices = []
    for path in (ROOT / "data" / "voices").glob("*"):
        if path.is_dir():
            has_profile = (path / "profile.json").exists()
            voices.append({"name": path.name, "profile": has_profile})
    OUT["voices"] = voices

    print(json.dumps(OUT, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
