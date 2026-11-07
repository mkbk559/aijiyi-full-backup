"""抖音群聊的"主动聊天"：轮询群消息 → 让 aiji 想一句 → 发出去 → 回读。

为什么单独一个模块：QQ 那边是 AstrBot 插件吃 OneBot 事件流，抖音没有事件流，
只能轮询网页。但**"聊天"这件事必须和 QQ 一模一样**：同一个 aiji 服务、同一个人格、
同一份记忆，会话 id 用 `douyin:group:<群名>`（和 QQ 的 `qq:group:<号>` 并列）。

真机事实（2026-11-05）：
- 会话列表项 = `.conversationConversationItemwrapper`（名字 + 最后一条 + 时间）
- 打开会话后输入框 = `.messageEditori`（editor-kit，contenteditable），回车发送
- 消息条 = `.messageMessageBoxselectableRow`，里面
  `.MessageBoxMessageTitleavatarName`（发件人名）+ `.MessageItemTextbubbleContent`（正文）
- **必须用有头浏览器**：headless 打开 /chat 会 `TargetClosedError`
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any, Callable

from aiji.heart.platforms.base import PlatformError

CONV_ITEM = ".conversationConversationItemwrapper"
# 真机 class 全名 `zone-container editor-kit-container messageEditorinputArea`，
# 外层是 `messageEditorimChatEditorContainer`。`.messageEditori` 是被截断的名字，别用。
CHAT_EDITOR = ('[class*="messageEditorinputArea"], '
               '.messageEditorimChatEditorContainer [contenteditable="true"]')
STATE = Path("data/douyin_chat_state.json")

READ_JS = """
() => Array.from(document.querySelectorAll('.messageMessageBoxselectableRow')).map(r => ({
  sender: ((r.querySelector('.MessageBoxMessageTitleavatarName') || {}).innerText || '').trim(),
  text: ((r.querySelector('.MessageItemTextbubbleContent') || {}).innerText || '').trim()
})).filter(m => m.text)
"""


def msg_key(msg: dict[str, Any]) -> str:
    raw = f"{msg.get('sender','')}|{msg.get('text','')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_state(path: Path | str = STATE) -> dict[str, Any]:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 没有/坏了都当空状态
        return {}


def save_state(state: dict[str, Any], path: Path | str = STATE) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def open_group(session: Any, group: str, *, wait_rounds: int = 12) -> None:
    """打开 /chat 并点开指定群（名字里包含 `group` 就算命中）。"""
    session.goto("https://www.douyin.com/chat")
    items: list[Any] = []
    for _ in range(wait_rounds):
        session.wait(2000)
        items = session.page.query_selector_all(CONV_ITEM) or []
        if items:
            break
    if not items:
        raise PlatformError("empty_feed", "会话列表是空的（/chat 没加载出来）",
                            hint="headless 打开 /chat 会崩，必须有头")
    names: list[str] = []
    for item in items:
        try:
            label = (item.inner_text() or "").replace("\n", " ")
        except Exception:  # noqa: BLE001
            label = ""
        names.append(label[:26])
        if group and group in label:
            # 抖音这个会话列表偶发"点了没反应"（真机 2026-11-05 12:10 复现），
            # 所以点完要轮询输入框，等不到就再点一次，最多三次。
            for attempt in range(3):
                item.click(force=True)
                for _ in range(6):
                    session.wait(1500)
                    if session.page.query_selector(CHAT_EDITOR) is not None:
                        return
                if attempt == 1:
                    try:
                        item.click(force=True)
                    except Exception:  # noqa: BLE001
                        pass
            raise PlatformError("selector_missing", f"点开「{group}」后没有输入框",
                                hint=f"点过 3 次；找的是 {CHAT_EDITOR}")
    raise PlatformError("peer_not_found", f"会话列表里没有「{group}」",
                        hint="现有会话：" + " / ".join(names)[:200])


def read_messages(session: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = session.page.evaluate(READ_JS) or []
    return list(rows)[-limit:]


def pick_reply_target(messages: list[dict[str, Any]], *, self_name: str,
                      seen: set[str]) -> dict[str, Any] | None:
    """挑一条"该回"的消息：不是自己发的、没回过、最新的那条。"""
    for msg in reversed(messages):
        if not msg.get("text"):
            continue
        if self_name and self_name in (msg.get("sender") or ""):
            continue
        if msg_key(msg) in seen:
            continue
        return msg
    return None


def http_ask(text: str, *, session_id: str, sender: str = "",
             api: str = "http://127.0.0.1:8787/v1/chat/completions",
             timeout: float = 90.0) -> str:
    """让 aiji 服务按 QQ 那套（同 persona/记忆/历史）生成回复。"""
    import urllib.parse
    import urllib.request

    # HTTP 头只能是 latin-1：`douyin:group:玖玖大王万岁` 直接塞进去会
    # UnicodeEncodeError（真机 2026-11-05 12:12 踩到）。所以百分号编码，且固定用同一套编码
    # 才能保证同一个群落在同一个记忆会话里。
    session_header = urllib.parse.quote(session_id, safe=":")
    payload = {
        "model": "aiji",
        "user": sender or session_header,
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "temperature": 0.9,
        "max_tokens": 200,
        "aiji": {"tools": False, "history": True, "history_limit": 40,
                 "persona": None, "sender_name": sender},
    }
    req = urllib.request.Request(
        api, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-Aiji-Session": session_header})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()


def run_once(adapter: Any, group: str, *, ask: Callable[[str, str], str],
             self_name: str = "", dry_run: bool = False,
             min_interval_s: float = 180.0,
             state_path: Path | str = STATE) -> dict[str, Any]:
    """跑一轮：读群 → 挑一条要回的 → 生成 → 发 → 记账。

    `min_interval_s` 是"两条回复之间至少隔多久"：计划任务 5 分钟一次、手工也可能再跑一次，
    没有这个闸门就会在群里连发（2026-11-05 12:22/12:24 两次连发就是这么来的）。
    """
    reason = _asleep_reason()
    if reason and not dry_run:
        return {"group": group, "replied": False, "reason": reason}
    state = load_state(state_path)
    seen: set[str] = set(state.get("seen", []))
    last_ts = float(state.get("last_ts") or 0)
    messages: list[dict[str, Any]] = []

    if not dry_run and min_interval_s > 0 and last_ts:
        gap = time.time() - last_ts
        if gap < min_interval_s:
            return {"group": group, "replied": False,
                    "reason": f"距上次回复只有 {int(gap)}s（最小间隔 {int(min_interval_s)}s）"}

    def _read(session: Any) -> list[dict[str, Any]]:
        open_group(session, group)
        return read_messages(session)

    messages = adapter.chat_session(_read)
    target = pick_reply_target(messages, self_name=self_name, seen=seen)
    out: dict[str, Any] = {"group": group, "messages_seen": len(messages),
                           "target": target}
    if target is None:
        out["replied"] = False
        out["reason"] = "没有需要回的新消息"
        return out
    reply = str(ask(target["text"], target.get("sender") or "") or "").strip()
    out["reply"] = reply
    if not reply:
        out["replied"] = False
        out["reason"] = "模型没生成内容"
        return out
    if dry_run:
        out["replied"] = False
        out["reason"] = "dry_run"
        return out
    res = adapter.group(group, reply)
    seen.add(msg_key(target))
    state["seen"] = sorted(seen)[-200:]
    state["last_group"] = group
    state["last_ts"] = time.time()
    save_state(state, state_path)
    out["replied"] = True
    out["sent"] = dict(res.detail)
    return out


# 2026-11-05 真机：每 5 分钟一条、整整 12 条/小时，像机器人刷屏。
# 一个人不会这么说话，所以给"发"这件事加闸门（借 dsh-douyin-watch 的口径）：
#   - 同一个对象 20 分钟内只回一次；
#   - 全平台每小时最多 2 条、每天最多 12 条；
#   - 每次发之前随机等 0–90 秒，打散机械节奏。
_HOURLY_CAP = 2
_DAILY_CAP = 12


def _rate_ok(state: dict, now: float) -> tuple[bool, str]:
    log = [float(t) for t in (state.get("send_log") or [])]
    hour = [t for t in log if now - t < 3600]
    day = [t for t in log if now - t < 86400]
    if len(hour) >= _HOURLY_CAP:
        return False, f"这一小时已经发了 {len(hour)} 条（上限 {_HOURLY_CAP}）"
    if len(day) >= _DAILY_CAP:
        return False, f"今天已经发了 {len(day)} 条（上限 {_DAILY_CAP}）"
    return True, ""


def _note_sent(state: dict, now: float) -> None:
    log = [float(t) for t in (state.get("send_log") or [])]
    log.append(now)
    state["send_log"] = [t for t in log if now - t < 7 * 86400][-200:]


def list_peers(session: Any, *, wait_ms: int = 6000) -> list[str]:
    """列出会话列表里的名字（群和私聊混在一起，按列表顺序）。"""
    session.goto("https://www.douyin.com/chat")
    session.wait(wait_ms)
    peers: list[str] = []
    for item in session.page.query_selector_all(CONV_ITEM) or []:
        try:
            label = (item.inner_text() or "").replace("\n", " ")
        except Exception:  # noqa: BLE001
            label = ""
        name = label.split(" ")[0].strip()
        if name and name not in peers:
            peers.append(name)
    return peers


def _asleep_reason() -> str:
    """按作息表判断"这个点该不该说话"。上课/睡觉一律不说（严格时间规划）。"""
    try:
        from aiji.brain import day as day_mod
        from aiji.brain import growth as growth_mod

        block = day_mod.current_block(stage_id=growth_mod.current().stage().id)
    except Exception:  # noqa: BLE001
        return ""
    if block.get("talk") == "none":
        return f"这个点（{block.get('start')}-{block.get('end')}）在{block.get('what')}，不说话"
    return ""


def run_all(adapter: Any, *, ask: Callable[[str, str], str], self_name: str = "",
            dry_run: bool = False, max_peers: int = 8,
            min_interval_s: float = 1200.0,
            state_path: Path | str = STATE) -> dict[str, Any]:
    """所有会话扫一轮。每个会话独立限流（state["peers"][名字] = 上次回复时间）。"""
    reason = _asleep_reason()
    if reason and not dry_run:
        return {"peers": [], "replied": 0, "reason": reason}
    state = load_state(state_path)
    seen: set[str] = set(state.get("seen", []))
    sent_by_us: set[str] = set(state.get("sent", []))
    per_peer: dict[str, float] = dict(state.get("peers") or {})
    peers = adapter.chat_session(lambda s: list_peers(s))[:max_peers]
    results: list[dict[str, Any]] = []

    for peer in peers:
        row: dict[str, Any] = {"peer": peer}
        last = float(per_peer.get(peer) or 0)
        if not dry_run and min_interval_s > 0 and last and (time.time() - last) < min_interval_s:
            row["reason"] = f"距上次回 {peer} 只有 {int(time.time()-last)}s"
            results.append(row)
            continue
        try:
            messages = adapter.chat_session(
                lambda s, p=peer: (open_group(s, p), read_messages(s))[1])
            target = pick_reply_target(messages, self_name=self_name, seen=seen)
            row["messages_seen"] = len(messages)
            if target is None:
                row["reason"] = "没有新话"
                results.append(row)
                continue
            text = str(target.get("text") or "").strip()
            if text in sent_by_us:
                row["reason"] = "那是我自己说的"
                results.append(row)
                continue
            row["target"] = text[:40]
            reply = str(ask(text, target.get("sender") or peer) or "").strip()
            if not reply:
                row["reason"] = "模型没说话"
                results.append(row)
                continue
            row["reply"] = reply[:40]
            if dry_run:
                row["reason"] = "dry_run"
                results.append(row)
                continue
            ok, why = _rate_ok(state, time.time())
            if not ok:
                row["reason"] = why
                results.append(row)
                continue
            time.sleep(random.uniform(0, 90))   # 打散机械节奏
            out = adapter.group(peer, reply)
            row["sent"] = out.detail if hasattr(out, "detail") else None
            row["replied"] = True
            seen.add(msg_key(target))
            sent_by_us.add(reply)
            per_peer[peer] = time.time()
            _note_sent(state, time.time())
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
        results.append(row)

    state["seen"] = sorted(seen)[-400:]
    state["sent"] = sorted(sent_by_us)[-400:]
    state["peers"] = per_peer
    save_state(state, state_path)
    return {"peers": results, "replied": sum(1 for r in results if r.get("replied"))}
