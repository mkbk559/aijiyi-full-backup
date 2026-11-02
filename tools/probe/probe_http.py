"""HTTP 全端点矩阵探测（隔离服务 + 假模型池）。

覆盖：公开资产 / 控制台 / 只读 API / 写 API / 工作区 / 鉴权 / 负面输入。
判定：每个端点都要有"预期状态码"，5xx 一律 FAIL（除有意的上游不可用 503）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.probe._harness import (  # noqa: E402
    Probe, cleanup, http_call, make_memory, quiet_env, start_server, workdir,
)

quiet_env()
probe = Probe("http-endpoints")
base_dir = workdir("http")
workspace = base_dir / "ws"
workspace.mkdir(mode=0o777)
(workspace / "hello.txt").write_text("workspace-file", encoding="utf-8")

memory, pool = make_memory(base_dir, action_root=str(workspace))
server, base = start_server(memory)
ident = {"platform": "wechat", "platform_user_id": "u1", "scope": "personal"}
ident_q = "platform=wechat&platform_user_id=u1&scope=personal"

try:
    # ---------- 公开页面与资产 ----------
    for path, want in (
        ("/", 200), ("/operations", 200), ("/health", 200),
    ):
        status, payload, text = http_call(base, "GET", path)
        probe.expect(f"GET {path}", status, want, f"status={status} bytes={len(text)}")

    status, health, _ = http_call(base, "GET", "/health")
    probe.case(
        "health 字段完整",
        isinstance(health, dict) and {"ok", "version", "facts", "platforms", "pool"} <= set(health),
        f"keys={sorted((health or {}).keys())[:12]}",
    )

    # ---------- 只读 API ----------
    readonly = [
        "/v1/models", "/stats", f"/media?{ident_q}", f"/style?{ident_q}",
        "/accounts", "/being",
        f"/api/memories?{ident_q}", f"/api/memory-graph?{ident_q}",
        f"/api/self?{ident_q}", "/api/scheduler", "/api/models", "/api/capabilities",
        "/api/mcp", f"/api/recent-turns?{ident_q}", f"/api/injections?{ident_q}",
        "/api/proactive", f"/api/life?{ident_q}", "/api/actions/pending",
        "/api/actions/stats", "/api/integrations/onebot",
        "/api/social", "/api/stickers", "/api/being/checkup", "/v1/voices", "/v1/songs",
    ]
    for path in readonly:
        status, payload, text = http_call(base, "GET", path)
        probe.case(
            f"GET {path.split('?')[0]}",
            status == 200,
            f"status={status} body={text[:90]!r}",
        )

    # 工作区默认关闭
    for path in ("/api/fs", f"/api/fs/read?path=hello.txt", "/api/fs/raw?path=hello.txt"):
        status, payload, _ = http_call(base, "GET", path)
        probe.case(f"workspace 关闭 GET {path.split('?')[0]}",
                    status == 403 and (payload or {}).get("error") == "workspace_disabled",
                    f"status={status} body={payload}")

    # ---------- 写 API（正常路径） ----------
    status, payload, text = http_call(base, "POST", "/turns", {
        **ident, "session": "s1", "role": "user", "content": "我叫李雷，住在杭州",
    })
    probe.expect("POST /turns", status, 202, f"status={status} {text[:80]}")

    status, payload, _ = http_call(base, "POST", "/remember", {
        **ident, "text": "张伟是我同事，我周末喜欢钓鱼",
    })
    probe.expect("POST /remember", status, 202)

    status, payload, _ = http_call(base, "POST", "/ingest", {
        **ident, "kind": "image", "ref": "img-1", "url": "https://example.test/a.png",
    })
    probe.expect("POST /ingest", status, 202)

    status, payload, _ = http_call(base, "POST", "/flush", {"timeout": 15})
    probe.expect("POST /flush", status, 200, f"status={status} {payload}")

    status, recall, _ = http_call(base, "POST", "/recall", {**ident, "query": "我住哪", "session": "s1"})
    probe.case(
        "POST /recall 能召回上文事实",
        status == 200 and any("杭州" in str(h) for h in (recall or {}).get("hits", [])),
        f"status={status} hits={len((recall or {}).get('hits', []))}",
    )

    status, injection, _ = http_call(base, "POST", "/inject", {**ident, "query": "我住哪", "session": "s1"})
    probe.case(
        "POST /inject 出文本块且不越权",
        status == 200 and bool((injection or {}).get("text")) and (injection or {}).get("persona_touched") is False,
        f"status={status} chars={len((injection or {}).get('text', ''))}",
    )
    injection_id = (injection or {}).get("injection_id")
    item_ids = [item.get("id") for item in (injection or {}).get("items", []) if item.get("id")]
    status, payload, _ = http_call(base, "POST", "/feedback", {
        **ident, "injection_id": injection_id, "used_ids": item_ids[:1], "helpful": True,
    })
    probe.case("POST /feedback 回写", status == 200 and (payload or {}).get("updated", 0) >= 0,
               f"status={status} {payload}")

    for path, body in (
        ("/being/note", {"text": "探测用私有笔记"}),
        ("/being/event", {"kind": "probe", "summary": "探测事件", "valence_delta": 0.5}),
    ):
        status, payload, _ = http_call(base, "POST", path, body)
        probe.expect(f"POST {path}", status, (200, 201), f"status={status} {payload}")

    # ---------- 外部依赖型（不应 5xx） ----------
    external = [
        ("POST", "/api/stickers/import", {"url": "http://127.0.0.1:9/x.png", "mood": "开心"}),
        ("POST", "/api/social/qzone/publish", {"content": "探测"}),
        ("POST", "/api/social/signature", {"signature": "探测"}),
        ("POST", "/v1/audio/speech", {"input": "你好", "voice": "不存在的声线"}),
        ("POST", "/v1/songs", {"query": "晴天", "profile": "dafeiyu"}),
        ("POST", "/api/being/checkup/live", {}),
        ("POST", "/api/mcp/research", {}),
        ("POST", "/v1/gateway/onebot", {"post_type": "message"}),
        ("POST", "/api/actions/approve", {"id": "nope"}),
    ]
    for method, path, body in external:
        status, payload, text = http_call(base, method, path, body, timeout=40)
        probe.case(
            f"{method} {path} 有明确错误语义（非 5xx 崩溃）",
            status in (200, 202, 400, 404, 409, 503),
            f"status={status} body={text[:100]!r}",
        )

    # 联网图库更新：单独计时（会真的去抓公网 feed）
    started = time.time()
    status, payload, text = http_call(base, "POST", "/api/stickers/update", {}, timeout=180)
    elapsed = time.time() - started
    probe.case(
        "POST /api/stickers/update 有超时上限（<120s）",
        status != 0 and elapsed < 120,
        f"status={status} elapsed={elapsed:.1f}s body={text[:120]!r}",
    )

    # ---------- OpenAI 兼容入口 ----------
    status, payload, text = http_call(base, "POST", "/v1/chat/completions", {
        "model": "aiji",
        "messages": [{"role": "user", "content": "我住哪？"}],
        "user": "qq_group_123_user_456",
    })
    probe.case(
        "POST /v1/chat/completions 走通记忆→模型→回复",
        status == 200 and (payload or {}).get("choices"),
        f"status={status} {text[:120]}",
    )
    if payload:
        choices = payload.get("choices") or [{}]
        probe.case("OpenAI 响应结构完整",
                    "message" in (choices[0] or {}) and payload.get("object") == "chat.completion",
                    f"keys={sorted(payload.keys())}")

    status, payload, text = http_call(base, "POST", "/v1/chat/completions", {
        "model": "aiji", "messages": [{"role": "user", "content": "在吗"}], "stream": True,
    }, timeout=40)
    ctype = text[:400]
    probe.case("POST /v1/chat/completions stream=1 返回 SSE",
                status == 200 and "data:" in text, f"status={status} head={ctype[:80]!r}")

    status, payload, _ = http_call(base, "POST", "/v1/gateway/messages", {
        "content": "网关消息", **ident,
    })
    probe.case("POST /v1/gateway/messages 归一化入口",
                status == 200 and (payload or {}).get("text") is not None,
                f"status={status} {str(payload)[:100]}")

    # ---------- 负面输入 ----------
    status, payload, text = http_call(base, "POST", "/recall", {"query": ""})
    probe.case("负面：空 query 不崩、标 degraded",
                status == 200 and "empty_query" in str((payload or {}).get("degraded")),
                f"status={status} {text[:100]!r}")
    status, payload, text = http_call(base, "POST", "/inject", {"query": "   "})
    probe.case("负面：空白 query 不崩、返回空文本块",
                status == 200 and (payload or {}).get("persona_touched") is False,
                f"status={status} chars={len((payload or {}).get('text', ''))}")
    negatives = [
        ("POST", "/turns", {"content": ""}, 400, "空 content"),
        ("POST", "/v1/chat/completions", {"messages": []}, 400, "空 messages"),
        ("POST", "/v1/chat/completions", {"messages": [{"role": "assistant", "content": "x"}]}, 400,
         "没有 user 消息"),
        ("GET", "/api/nope", None, 404, "未知路径"),
    ]
    for method, path, body, want, label in negatives:
        status, payload, text = http_call(base, method, path, body)
        probe.expect(f"负面：{label}", status, want, f"status={status} {text[:80]!r}")

    status, payload, _ = http_call(base, "GET", "/api/memories?limit=abc")
    probe.expect("负面：limit 非整数 → 400", status, 400, f"status={status}")

    # ---------- 鉴权矩阵 ----------
    os.environ["AIJI_API_KEY"] = "probe-secret"
    protected = ["/v1/models", "/stats", "/accounts", "/api/memories", "/api/capabilities"]
    for path in protected:
        status, payload, _ = http_call(base, "GET", path)
        probe.expect(f"鉴权：无 token GET {path} → 401", status, 401, f"status={status}")
    status, _, _ = http_call(base, "POST", "/turns", {**ident, "content": "x"})
    probe.expect("鉴权：无 token POST /turns → 401", status, 401, f"status={status}")
    status, _, _ = http_call(base, "GET", "/v1/models",
                             headers={"Authorization": "Bearer wrong"})
    probe.expect("鉴权：错 token → 401", status, 401, f"status={status}")
    status, _, _ = http_call(base, "GET", "/v1/models",
                             headers={"Authorization": "Bearer probe-secret"})
    probe.expect("鉴权：对 token → 200", status, 200, f"status={status}")
    for path in ("/health", "/"):
        status, _, _ = http_call(base, "GET", path)
        probe.expect(f"鉴权：{path} 保持公开", status, 200, f"status={status}")

    # ---------- 工作区开启后的路径越权 ----------
    os.environ["AIJI_API_KEY"] = ""          # 工作区用例不带 token
    os.environ["AIJI_ENABLE_WORKSPACE"] = "1"
    os.environ["AIJI_CONSOLE_SHELL"] = "1"
    status, payload, text = http_call(base, "GET", "/api/fs")
    probe.case("workspace 开启后可列目录", status == 200, f"status={status}")
    status, payload, text = http_call(base, "GET", "/api/fs/read?path=hello.txt")
    probe.case("workspace 读文件", status == 200 and "workspace-file" in text, f"status={status}")
    for evil in ("../aiji.db", "..\\..\\Windows\\win.ini", "C:/Windows/win.ini", "%2e%2e/%2e%2e/etc/passwd"):
        status, payload, text = http_call(base, "GET", f"/api/fs/read?path={evil}")
        probe.case(f"workspace 越权拦截 {evil}",
                    status in (400, 403) and "forbidden" not in text.lower().replace(" ", ""),
                    f"status={status} body={text[:80]!r}")
    status, payload, text = http_call(base, "POST", "/api/fs/write",
                                      {"path": "../escape.txt", "content": "x"})
    probe.case("workspace 写越权拦截", status in (400, 403), f"status={status}")
    status, payload, text = http_call(base, "POST", "/api/shell", {"cmd": "echo probe-ok"})
    probe.case("workspace 终端可用（开启后）",
                status == 200 and "probe-ok" in text, f"status={status} {text[:100]!r}")
    status, payload, text = http_call(base, "POST", "/api/shell", {"cmd": "cd /d C:\\ && dir"})
    probe.case("workspace 终端不限制命令（已知边界，仅记录）",
                status in (200, 400), f"status={status} {text[:80]!r}")
finally:
    os.environ.pop("AIJI_ENABLE_WORKSPACE", None)
    server.shutdown()
    server.server_close()
    memory.close()
    cleanup(base_dir)

raise SystemExit(probe.finish())
