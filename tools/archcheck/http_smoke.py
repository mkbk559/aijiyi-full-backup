"""HTTP 冒烟：起服务（子进程）→ 直连（绕过 Clash 代理）→ 断言页面与接口 → 关掉。

做成**真断言**（2026-11-03 修）：任何一条不符合预期就以非 0 退出，不再只是打印。
顺手修了个陈旧期望——脚本原来检查 `/api/operations/overview`，这条路**本来就不存在**
（真实路由是 features / config / services），404 是正确行为，是脚本自己过时了。
另外加一条负向对照：不存在的路径必须 404，否则说明路由兜底太宽。
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = 18787
BASE = f"http://127.0.0.1:{PORT}"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PATHS = [
    "/api/operations/features",
    "/api/operations/config",
    "/api/operations/services",
    "/api/models",
    "/api/capabilities",
    "/api/life",
    "/v1/models",
]
MUST_404 = ["/api/operations/overview", "/api/definitely-not-a-route"]

proc = subprocess.Popen(
    [sys.executable, "-m", "aiji.senses.cli", "--db",
     "tools/archcheck/results/smoke.db", "serve", "--port", str(PORT)],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
failures = []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        failures.append(msg)


try:
    html = ""
    for _ in range(60):
        try:
            with opener.open(BASE + "/", timeout=2) as r:
                html = r.read().decode("utf-8", "replace")
            break
        except Exception:
            if proc.poll() is not None:
                print("server exited early rc=", proc.returncode)
                print((proc.stdout.read() or "")[-2000:])
                raise SystemExit(1)
            time.sleep(0.5)

    check(bool(html), f"GET / 返回非空（{len(html)} B）")
    check("AIJiYi" in html, "首页含标题 AIJiYi")
    check(all(v in html for v in ("view-overview", "view-features", "view-settings", "view-lifecycle")),
          "首页含四个视图容器")

    for path in PATHS:
        try:
            with opener.open(BASE + path, timeout=10) as r:
                body = r.read()
                code = r.status
                parsed = True
                try:
                    json.loads(body.decode("utf-8"))
                except Exception:
                    parsed = False
                check(code == 200 and len(body) > 1, f"GET {path} -> {code} {len(body)}B")
                check(parsed, f"GET {path} 返回合法 JSON")
        except Exception as e:
            check(False, f"GET {path} -> {e}")

    for path in MUST_404:
        try:
            with opener.open(BASE + path, timeout=10) as r:
                check(False, f"GET {path} 应 404，实际 {r.status}")
        except urllib.error.HTTPError as e:
            check(e.code == 404, f"GET {path} -> 404（预期）")
        except Exception as e:
            check(False, f"GET {path} -> 意外错误 {e}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

print(f"\nSUMMARY failures={len(failures)}")
raise SystemExit(1 if failures else 0)
