"""按验收清单跑自动可验的 P0/P3/P4 项，输出 JSON 供填表。"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\AIJiYi")
BASE = "http://127.0.0.1:8787"
A = "2216608312"
B = "2221813100"
GROUP = "562686653"


def auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    key = os.environ.get("AIJI_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def get(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, method="GET", headers=auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def post(path: str, body: dict, headers: dict | None = None) -> tuple[int, dict, str]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=auth_headers({"Content-Type": "application/json", **(headers or {})}),
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw), raw
            except ValueError:
                return resp.status, {}, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return exc.code, {}, raw


def main() -> int:
    result: dict = {}
    status, health = get("/health")
    result["health"] = {"status": status, "ok": health.get("ok")}

    fa_status, fa = get("/api/memories?" + urllib.parse.urlencode({
        "platform": "qq", "platform_user_id": A, "scope": f"group:{GROUP}"}))
    fb_status, fb = get("/api/memories?" + urllib.parse.urlencode({
        "platform": "qq", "platform_user_id": B, "scope": f"group:{GROUP}"}))
    result["facts_a"] = [x.get("text") for x in fa.get("items", [])]
    result["facts_b"] = [x.get("text") for x in fb.get("items", [])]
    result["cross_talk"] = {
        "a_has_b": any("笼子" in (x or "") for x in result["facts_a"]),
        "b_has_a": any("花园" in (x or "") for x in result["facts_b"]),
    }

    ta_status, ta = get("/api/recent-turns?" + urllib.parse.urlencode({
        "platform": "qq", "platform_user_id": A,
        "scope": f"group:{GROUP}", "limit": 100}))
    tb_status, tb = get("/api/recent-turns?" + urllib.parse.urlencode({
        "platform": "qq", "platform_user_id": B,
        "scope": f"group:{GROUP}", "limit": 100}))
    result["turns"] = {"a": len(ta.get("items", [])), "b": len(tb.get("items", []))}

    body = {
        "model": "aiji", "user": f"qq_group_{GROUP}_user_{A}",
        "messages": [{"role": "user", "content": "我叫什么，住在哪里"}],
        "temperature": 0.9, "max_tokens": 160,
        "aiji": {"tools": False, "history": True, "persona": "sunba"},
        "stream": False,
    }
    code, payload, raw = post("/v1/chat/completions", body,
                              {"X-Aiji-Session": f"group:{GROUP}"})
    reply = ""
    if payload:
        reply = str(payload.get("choices", [{}])[0].get("message", {}).get("content", ""))
    result["reply_a"] = {"status": code, "reply": reply,
                         "has_garden": "花园" in reply}

    style_body = dict(body)
    style_body["messages"] = [{"role": "user", "content": "在吗"}]
    code2, payload2, _ = post("/v1/chat/completions", style_body,
                              {"X-Aiji-Session": f"group:{GROUP}"})
    style_reply = ""
    if payload2:
        style_reply = str(payload2.get("choices", [{}])[0].get("message", {}).get("content", ""))
    banned = ["有什么可以帮", "想聊点什么", "抱歉", "我只是一条", "出不了鱼缸"]
    result["style_reply"] = {
        "reply": style_reply,
        "banned_hit": [b for b in banned if b in style_reply],
    }

    fs_status, _ = get("/api/fs?path=")
    result["workspace_status"] = fs_status

    req = urllib.request.Request(BASE + "/")
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", "replace")
    result["console"] = {
        "nav_buttons": html.count("data-view="),
        "has_workspace_nav": "data-view=\"workspace\"" in html,
    }

    conn = sqlite3.connect(ROOT / "data" / "aiji.db")
    conn.row_factory = sqlite3.Row
    result["db"] = {
        "turn_qq": conn.execute(
            "SELECT count(*) FROM turn WHERE platform='qq'").fetchone()[0],
        "summary_a": conn.execute(
            "SELECT count(*) FROM summary WHERE platform='qq' AND platform_user_id=?",
            (A,)).fetchone()[0],
        "summary_b": conn.execute(
            "SELECT count(*) FROM summary WHERE platform='qq' AND platform_user_id=?",
            (B,)).fetchone()[0],
        "group_turns": conn.execute(
            "SELECT count(*) FROM turn WHERE platform='qq' AND scope=?",
            (f"group:{GROUP}",)).fetchone()[0],
    }
    conn.close()

    result["persona"] = {
        "file": (ROOT / "data" / "persona" / "sunba.json").exists(),
        "traits": [],
    }
    try:
        p = json.loads((ROOT / "data" / "persona" / "sunba.json").read_text("utf-8"))
        result["persona"]["traits"] = p.get("traits", [])
        result["persona"]["examples"] = len(p.get("examples", []))
    except Exception as exc:  # noqa: BLE001
        result["persona"]["error"] = str(exc)

    result["login_state"] = (ROOT / "data" / "browser" / "baidu_state.json").exists()

    plugin = (Path(r"D:\AstrBot\data\plugins\aiji_memory\main.py")).read_text("utf-8")
    result["plugin"] = {
        "has_ingest": "_ingest" in plugin,
        "has_learn": "LEARN_CUES" in plugin,
        "has_voice": "_tts" in plugin,
        "has_persona": '"persona": "sunba"' in plugin,
    }

    try:
        s = socket.create_connection(("127.0.0.1", 6199), timeout=3)
        s.close()
        result["onebot_6199"] = True
    except OSError:
        result["onebot_6199"] = False

    log = Path(r"D:\AstrBot\astrbot-run9.log").read_text("utf-8", "replace")
    result["astrbot_connected"] = "适配器已连接" in log

    unit = (ROOT / ".tmp" / "unittest-checklist.log").read_text("utf-8", "replace")
    result["unit_tests"] = {
        "ran": [line for line in unit.splitlines() if line.startswith("Ran ")],
        "ok": "OK" in unit.splitlines()[-1] if unit.splitlines() else False,
    }

    out = ROOT / ".tmp" / "acceptance_probe.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
