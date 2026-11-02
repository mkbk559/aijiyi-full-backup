"""HTTP 冒烟：起服务（子进程）→ 直连（绕过 Clash 代理）→ 断言页面/接口 → 关掉。"""
import json
import subprocess
import sys
import time
import urllib.request

PORT = 18787
BASE = f"http://127.0.0.1:{PORT}"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

proc = subprocess.Popen(
    [sys.executable, "-m", "aiji.senses.cli", "--db", "tools/archcheck/results/smoke.db", "serve", "--port", str(PORT)],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
ok = False
try:
    for _ in range(40):
        try:
            with opener.open(BASE + "/", timeout=2) as r:
                html = r.read().decode("utf-8", "replace")
            ok = True
            break
        except Exception:
            if proc.poll() is not None:
                print("server exited early rc=", proc.returncode)
                print(proc.stdout.read()[-2000:])
                raise SystemExit(1)
            time.sleep(0.5)
    print("GET / ok=", ok, "bytes=", len(html), "has_title=", "AIJiYi" in html,
          "has_views=", all(v in html for v in ("view-overview", "view-features", "view-settings", "view-lifecycle")))
    for path in ("/api/operations/overview", "/api/operations/services", "/v1/models"):
        try:
            with opener.open(BASE + path, timeout=5) as r:
                body = r.read()
                print(f"GET {path} -> {r.status} {len(body)}B")
        except Exception as e:
            print(f"GET {path} -> FAILED {e}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("server stopped, rc=", proc.returncode)
