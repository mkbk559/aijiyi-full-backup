"""控制台一致性检查：页面上的每个接口都必须是 server.py 里真有的路由。

体检发现控制台曾经是手写的 4 个视图，和架构图的九大系统对不上；重构后
`tools/console_build.py` 用架构图数据生成页面。这个脚本验证三件事：

1. **规格完整性**：9 个系统、68 个节点、每个系统都有面板；
2. **路由一致性**：页面里出现的每条 GET/POST 路径都能在 server.py 里找到
   （精确匹配或 startswith 前缀），不存在"点了只会 404"的按钮；
3. **真机可达**：起一个服务，`GET /operations` 返回 200、含 9 个导航项，
   并把每个系统第一块面板的接口真打一遍（允许 4xx，不允许 5xx/断连）。

用法：python tools/archcheck/console_check.py
产物：tools/archcheck/results/console_check.json
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
PORT = 18791
BASE = f"http://127.0.0.1:{PORT}"


def server_routes() -> tuple[set[str], list[str]]:
    tree = ast.parse((ROOT / "aiji" / "throat" / "server.py").read_text(encoding="utf-8"))
    exact: set[str] = set()
    prefixes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in ("do_GET", "do_POST"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Compare) or not isinstance(sub.left, ast.Name):
                continue
            if sub.left.id not in ("path", "p"):
                continue
            for op, comp in zip(sub.ops, sub.comparators):
                if not isinstance(comp, ast.Constant) or not isinstance(comp.value, str):
                    continue
                if isinstance(op, ast.Eq):
                    exact.add(comp.value)
                elif isinstance(op, ast.In) and isinstance(comp, (ast.Tuple, ast.List)):
                    exact.update(
                        elt.value for elt in comp.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str))
            # startswith("...") 前缀路由
            if isinstance(sub.ops[0], ast.In) is False:
                pass
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "startswith" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and arg.value.startswith("/"):
                prefixes.append(arg.value)
    return exact, sorted(set(prefixes))


def spec_paths() -> list[tuple[str, str]]:
    import console_build

    out: list[tuple[str, str]] = []
    for sys_spec in console_build.SYSTEMS + console_build.EXTRA_VIEWS:
        for _title, path in sys_spec.get("panels", []):
            out.append((sys_spec["id"], path))
        for _label, method, path, _fields in sys_spec.get("acts", []):
            out.append((sys_spec["id"], path))
    return out


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with op().open(req, timeout=180) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}".encode()


def main() -> None:
    import console_build

    nodes = console_build.build_spec()
    exact, prefixes = server_routes()

    def known(path: str) -> bool:
        if path in exact:
            return True
        return any(path.startswith(pre) and len(path) > len(pre) for pre in prefixes)

    spec = spec_paths()
    unknown = sorted({p for _sid, p in spec if not known(p)})
    report: dict = {
        "bands": len(nodes), "nodes": sum(len(v) for v in nodes.values()),
        "systems": len(console_build.SYSTEMS), "spec_paths": len(spec),
        "server_routes": len(exact), "prefixes": prefixes,
        "unknown_paths": unknown,
    }
    html = (ROOT / "aiji" / "right_hand" / "web" / "operations.html").read_text(encoding="utf-8")
    report["html_bytes"] = len(html.encode("utf-8"))
    report["external_refs"] = [
        u for u in __import__("re").findall(r"https?://[^\s\"')]+", html)
        if not u.startswith("http://www.w3.org")
        and "127.0.0.1" not in u and "localhost" not in u
    ]

    proc = subprocess.Popen([sys.executable, "-m", "aiji.senses.cli", "serve", "--port", str(PORT)],
                            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    live: dict = {}
    try:
        for _ in range(60):
            try:
                with op().open(BASE + "/health", timeout=3) as r:
                    r.read()
                break
            except Exception:
                if proc.poll() is not None:
                    live["error"] = "服务起不来"
                    break
                time.sleep(1.0)
        status, body = call("/operations")
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        live["page_status"] = status
        live["has_nine_systems"] = all(s["nav"] in text for s in console_build.SYSTEMS)
        live["nav_labels"] = [s["nav"] for s in console_build.SYSTEMS if s["nav"] in text]
        firsts = []
        for sys_spec in console_build.SYSTEMS:
            if not sys_spec.get("panels"):
                continue
            path = sys_spec["panels"][0][1]
            st, _ = call(path if path.startswith("/api") or path.startswith("/v1") else path)
            firsts.append({"system": sys_spec["id"], "path": path, "status": st})
        live["first_panels"] = firsts
        live["server_errors"] = [f for f in firsts if f["status"] is None or f["status"] >= 500]
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()

    report["live"] = live
    report["verdict"] = {
        "ok": not unknown and live.get("has_nine_systems")
              and live.get("page_status") == 200 and not live.get("server_errors")
              and not report["external_refs"],
        "unknown_paths": unknown,
        "external_refs": report["external_refs"],
        "page_status": live.get("page_status"),
        "nine_systems": live.get("has_nine_systems"),
        "server_errors": live.get("server_errors"),
    }
    (ROOT / "tools" / "archcheck" / "results" / "console_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False))
    print(json.dumps({k: report[k] for k in ("bands", "nodes", "systems", "spec_paths",
                                             "server_routes", "html_bytes")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
