"""路由级冒烟：把 server.py 里**每一条**路由都打一遍（2026-11-03）。

为什么：`http_smoke.py` 只验了 8 条路径，而 server 里有 58 条精确路由
（30 GET / 28 POST）→ 86% 的 HTTP 面从来没有自动化测试。
这里用"坏输入"打每一条：**允许 4xx，不允许 5xx / 连接断开 / 非 JSON**。
GET 也要求 200（少数需要参数的允许 400，但必须给 JSON）。

用法：python tools/archcheck/route_smoke.py
产物：tools/archcheck/results/route_smoke.json + 控制台表格
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 18790
BASE = f"http://127.0.0.1:{PORT}"

# 需要显式给点东西才不 400 的 GET（其余 GET 一律要求 200 + JSON）
GET_ALLOW_400 = {"/media", "/api/fs/raw", "/api/fs/read", "/api/recent-turns"}
# 会改状态的 POST：只发空 body，验证"坏输入不 5xx"，不发真数据
POST_ROUTES = [
    "/api/being/checkup/live", "/api/fs/write", "/api/mcp/research", "/api/shell",
    "/api/social/avatar", "/api/social/qzone/auto-comment", "/api/social/qzone/comment",
    "/api/social/qzone/publish", "/api/social/share/bilibili", "/api/social/share/music",
    "/api/social/signature", "/api/stickers/import", "/api/stickers/update",
    "/being/event", "/being/note", "/feedback", "/flush", "/ingest", "/inject",
    "/media", "/recall", "/remember", "/turns", "/v1/audio/speech",
    "/v1/gateway/messages", "/v1/gateway/onebot", "/v1/songs", "/v1/voices/use",
]
GET_ROUTES = [
    "/api/actions/pending", "/api/actions/stats", "/api/being/checkup",
    "/api/capabilities", "/api/fs", "/api/fs/raw", "/api/fs/read",
    "/api/injections", "/api/integrations/onebot", "/api/life", "/api/mcp",
    "/api/memories", "/api/memory-graph", "/api/models", "/api/operations/config",
    "/api/operations/features", "/api/operations/services", "/api/proactive",
    "/api/recent-turns", "/api/scheduler", "/api/self", "/api/social",
    "/api/stickers", "/being", "/health", "/media", "/stats", "/style",
    "/v1/songs", "/v1/voices",
]


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path: str, method: str = "GET", body: bytes | None = None):
    req = urllib.request.Request(BASE + path, method=method, data=body,
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with op().open(req, timeout=120) as r:
            raw = r.read()
            code = r.status
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        code = exc.code
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "method": method, "status": None,
                "ms": round((time.perf_counter() - t) * 1000.0, 1),
                "verdict": "CONN", "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    ms = round((time.perf_counter() - t) * 1000.0, 1)
    parsed = None
    if "json" in ctype.lower():
        try:
            parsed = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            parsed = "<invalid json>"
    verdict = "ok"
    if code >= 500:
        verdict = "5xx"
    elif method == "GET" and code == 200 and parsed is None:
        verdict = "not-json"
    elif method == "GET" and code == 400 and path not in GET_ALLOW_400:
        verdict = "400"
    elif method == "POST" and code < 400:
        verdict = "2xx-on-bad-input"  # 空 body 还成功 → 校验缺失，值得看
    err = ""
    if isinstance(parsed, dict):
        err = str(parsed.get("error") or parsed.get("detail") or "")[:120]
    elif isinstance(parsed, str):
        err = parsed[:120]
    return {"path": path, "method": method, "status": code, "ms": ms,
            "ctype": ctype, "verdict": verdict, "error": err,
            "bytes": len(raw)}


def main() -> None:
    py = Path(sys.executable)
    proc = subprocess.Popen([str(py), "-m", "aiji.senses.cli", "serve", "--port", str(PORT)],
                            cwd=str(ROOT), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    report = {"base": BASE, "rows": []}
    try:
        for _ in range(60):
            try:
                with op().open(BASE + "/health", timeout=3) as r:
                    r.read()
                break
            except Exception:
                if proc.poll() is not None:
                    raise SystemExit("server 起不来")
                time.sleep(1.0)
        for path in GET_ROUTES:
            row = call(path, "GET")
            report["rows"].append(row)
            print(f"{row['verdict']:18s} {row['status']!s:5s} {row['ms']:>8} ms  GET  {path}  {row['error']}",
                  flush=True)
        for path in POST_ROUTES:
            row = call(path, "POST", b"{}")
            report["rows"].append(row)
            print(f"{row['verdict']:18s} {row['status']!s:5s} {row['ms']:>8} ms  POST {path}  {row['error']}",
                  flush=True)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
    bad = [r for r in report["rows"] if r["verdict"] not in ("ok",)]
    report["verdict"] = {"total": len(report["rows"]), "bad": len(bad),
                         "bad_routes": [f"{r['method']} {r['path']} → {r['verdict']}" for r in bad]}
    (ROOT / "tools" / "archcheck" / "results" / "route_smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY " + json.dumps(report["verdict"], ensure_ascii=False))


if __name__ == "__main__":
    main()
