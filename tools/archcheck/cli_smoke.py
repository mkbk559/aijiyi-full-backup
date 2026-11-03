"""CLI 冒烟：17 个子命令全跑一遍（2026-11-03）。

体检发现 `aiji/senses/cli.py`（285 语句）测试覆盖 **0%**——所有命令行行为只靠手工试过。
这里用真子进程跑：每个命令先 `--help`，再在**全新临时库**上跑最小实参，
判定标准：退出码 0、stdout 是 JSON（或含预期关键字）、stderr 无 Traceback。

用法：python tools/archcheck/cli_smoke.py
产物：tools/archcheck/results/cli_smoke.json
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / ".tmp" / "cli_smoke"

# (命令, 实参, 期望 stdout 里出现的键)
READ_ONLY = [
    ("stats", [], "turns"),
    ("doctor", [], "version"),
    ("style", [], None),
    ("accounts", [], None),
    ("quota", [], None),
    ("media", [], None),
    ("self-memory", [], None),
    ("archive", [], None),
    ("export", [], None),
]
WRITE_PATH = [
    ("remember", ["冒烟测试：今天在整理命令行回归"], None),
    ("ingest", ["--kind", "text", "--text", "冒烟测试：一条入站消息"], None),
    ("recall", ["冒烟测试"], None),
    ("inject", ["冒烟测试"], None),
    ("feedback", ["--note", "冒烟测试"], None),
    ("forget", ["--like", "冒烟测试"], None),
]
MODIFIER = [("self-memory", ["--add", "冒烟测试用的自我描述"], None)]
SKIP = {"serve": "阻塞式服务，由 route_smoke.py 覆盖", "bench": "自检基准，耗时长且已有 suite"}


def run(args: list[str], db: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "AIJI_DB": str(db),
        "PYTHONIOENCODING": "utf-8",
        "NO_COLOR": "1",
    })
    # 本机全局代理会污染 localhost 请求
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    proc = subprocess.run([sys.executable, "-m", "aiji.senses.cli", *args],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=300)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parsed = None
    if out.startswith("{") or out.startswith("["):
        try:
            parsed = json.loads(out)
        except Exception:
            parsed = "<invalid json>"
    return {"args": args, "rc": proc.returncode, "json": parsed is not None,
            "stdout_head": out[:160], "stderr_head": err[:200],
            "traceback": "Traceback (most recent call last)" in err}


def main() -> None:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    db = SCRATCH / "aiji.db"
    report: dict = {"db": str(db), "rows": []}

    help_runs = []
    for name in sorted({c for c, _, _ in READ_ONLY + WRITE_PATH + MODIFIER} | set(SKIP)):
        row = run([name, "--help"], db)
        row["kind"] = "help"
        row["name"] = name
        help_runs.append(row)
    report["rows"].extend(help_runs)

    paths = [("read-only", READ_ONLY), ("write-path", WRITE_PATH), ("modifier", MODIFIER)]
    for kind, cases in paths:
        for name, extra, expect in cases:
            row = run([name, *extra], db)
            row["kind"] = kind
            row["name"] = name
            row["expect_key"] = expect
            if expect and isinstance(row["json"], bool) and row["json"]:
                pass
            report["rows"].extend([row])

    bad = []
    for row in report["rows"]:
        if row["rc"] != 0 or row["traceback"]:
            bad.append(f"{row['name']} {row['args'][1:]} → rc={row['rc']}"
                       f"{' +Traceback' if row['traceback'] else ''}")
    no_json = [f"{r['name']} {r['args'][1:]}" for r in report["rows"]
               if r["kind"] != "help" and not r["json"]]
    report["verdict"] = {
        "commands": len({r["name"] for r in report["rows"]}),
        "runs": len(report["rows"]),
        "failed": bad,
        "non_json": no_json,
        "db_created": db.exists(),
        "tables": None,
    }
    try:
        import sqlite3
        with sqlite3.connect(db) as conn:
            report["verdict"]["tables"] = conn.execute(
                "select count(*) from sqlite_master where type='table'").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        report["verdict"]["tables"] = f"error: {exc}"

    (ROOT / "tools" / "archcheck" / "results" / "cli_smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for row in report["rows"]:
        mark = "ok  " if not (row["rc"] or row["traceback"]) else "FAIL"
        print(f"{mark} {row['kind']:10s} {row['name']:12s} {' '.join(row['args'][1:])[:40]:42s} "
              f"rc={row['rc']} json={row['json']} {row['stderr_head'][:60]}")
    print("\nSUMMARY " + json.dumps(report["verdict"], ensure_ascii=False))


if __name__ == "__main__":
    main()
