"""好奇心闭环（B1，2026-11-07）：'我不知道X' → 去查 → 学成 knowledge。

为什么需要：三件联网工具（web_search/fetch_url/bili_search）11-05 上线后，
全库 0 次调用记录——被教"学习赚钱"时他绕了 20 轮"钱是从手机里出来的吗？
我猜的哦"也不会去查。工具挂在墙上，大脑没伸手。

设计：
- **被动触发**：`maybe_learn(query, reply)` 由 head 在回复后调用——回复里
  出现"不知道/没学过"类句式时，异步去查一次，结果写进 knowledge 事实。
- **主动学习**：`learn_topic(topic)` 任何地方都可以直接调（life_loop 刷视频、
  日记的"明天想学"）。查完写 knowledge + being_event(kind='curiosity')。
- 幂等/限流：同一 topic 7 天内只查一次（表里查得到就不重复），每小时最多
  2 次（不给积分网关加压）。
- 隔离：搜索走 ActionExecutor 的只读工具，失败静默降级——学习是加分项，
  不是主链路。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

# "我不知道X" 的句式家族。故意保守：宁可少触发，不要把普通聊天都拿去搜索。
_UNKNOWN_PAT = re.compile(
    r"(?:我(?:还|都)?(?:是)?不知道|我不太懂|我没学过|我不明白|不了解"
    r"|不懂(?:这个|这是什么)|什么是(.{2,12})[?？]|(.{2,12})是什么(?:意思|呀|啊|东西)[?？]?)"
)

# 每小时最多学几次（学习是后台的，别抢回复的算力）
_HOURLY_CAP = 2
# 学习结果写进 fact 的固定身份（学来的知识属于"我自己"，不挂在某个用户名下）
_LEARN_IDENT = ("local", "self", "default")

_state_lock = threading.Lock()
_recent_learns: list[float] = []
_seen_topics: dict[str, float] = {}


def extract_unknown_topic(query: str) -> str:
    """从"什么是X？/X是什么意思？"里抠出学习主题；不是疑问句就返回空。"""
    text = str(query or "").strip().strip("。，！!？?~～ \r\n")
    # 剔除 @ 提及和引用残留（群聊消息里常见）
    text = re.sub(r"@\S+", " ", text)
    m = re.search(r"什么是(.{2,16})", text)
    if m:
        return m.group(1).strip(" ?？.,，!！的呀啊吧呢哦嘛喽")
    m = re.search(r"(.{2,16})是什么(?:意思|东西|概念|梗)?", text)
    if m:
        topic = m.group(1).strip(" ?？.,，!！的呀啊吧呢哦嘛喽")
        # "小 X 是什么" 的"小"是修饰不是主题一部分——但别过度修剪
        return topic
    return ""


def wants_to_learn(reply_text: str, query: str) -> str:
    """回复里表现出"不知道"，且原问题是可提取的主题 → 返回主题；否则空。

    只在"问题有明确主题且回复承认不知道"时触发，普通"不知道你在说什么"
    （没有主题）不触发——那多半是斗嘴不是真求知。
    """
    reply = str(reply_text or "")
    if not _UNKNOWN_PAT.search(reply):
        return ""
    return extract_unknown_topic(query)


def _rate_ok(now: float) -> bool:
    with _state_lock:
        global _recent_learns
        _recent_learns = [t for t in _recent_learns if now - t < 3600]
        if len(_recent_learns) >= _HOURLY_CAP:
            return False
        _recent_learns.append(now)
        return True


def _topic_fresh(topic: str, now: float, ttl_s: float = 7 * 86400) -> bool:
    with _state_lock:
        last = _seen_topics.get(topic, 0.0)
        if now - last < ttl_s:
            return False
        _seen_topics[topic] = now
        # 防字典无限涨：清掉过期
        if len(_seen_topics) > 500:
            cutoff = now - ttl_s
            for key in [k for k, v in _seen_topics.items() if v < cutoff]:
                _seen_topics.pop(key, None)
        return True


def _search_web(topic: str, executor: Any) -> str:
    """跑一次 web_search 工具，把结果压成一段学习文本。"""
    from aiji.left_hand.actions.schema import ToolCall

    call = ToolCall(
        id=f"curiosity-{int(time.time())}",
        name="web_search",
        arguments={"query": topic, "limit": 4},
    )
    res = executor.execute(call)
    if not res.ok:
        return ""
    # 结果是 "1. 标题\n   url" 列表：标题给摘要，URL 给深读（_deep_read 用）
    return str(res.output or "").strip()


def _deep_read(topic: str, executor: Any, search_output: str) -> str:
    """深读（2026-11-07）：搜到标题只是"书名"，读到正文摘要才是真学习。

    实测网络边界（2026-11-07 真机）：百度百科/知乎/维基对脚本 UA 一律 403
    （带浏览器头也没用），维基超时。**Bing 结果页自身的 b_lineclamp 摘要段
    可达且内容质量高**（每条 2-3 句正文级摘要）。所以深读走两条路：
    ① fetch_url 读第一条结果 URL（对不反爬的站点有效）；
    ② 失败就 fetch Bing 搜索页抽 b_lineclamp 摘要段（本机的稳定路线）。
    都失败退回标题串——深读是加分项不是依赖项。
    """
    import urllib.parse

    from aiji.left_hand.actions.schema import ToolCall

    # 路 ①：读第一条结果页
    urls = [ln.strip() for ln in str(search_output or "").splitlines()
            if ln.strip().startswith("http")][:1]
    if urls:
        try:
            res = executor.execute(ToolCall(
                id=f"curiosity-read-{int(time.time())}",
                name="fetch_url",
                arguments={"url": urls[0], "max_chars": 4000},
            ))
            if res.ok:
                text = str(res.output or "").strip()
                paras = [p.strip() for p in text.splitlines()
                         if len(p.strip()) >= 30]
                body = "\n".join(paras[:3])[:1200]
                if len(body) >= 80:
                    return body
        except Exception:  # noqa: BLE001
            pass
    # 路 ②：Bing 搜索页摘要段（本机稳定源）。注意两点（真机翻过车）：
    # - 不走 fetch_url 工具——那条路 Accept-Language 头不全时 Bing 会随机给法语页；
    # - 查询只用主题词本身，**不带"是什么"**——带了 Bing 会把「光合作用」
    #   拆成「光」（真机实测：返回光学释义），纯主题词的摘要才是正文级。
    try:
        url = ("https://www.bing.com/search?q="
               + urllib.parse.quote(topic) + "&setmkt=zh-CN")
        html = executor._http_get(url, timeout_s=20.0,
                                  headers=executor._BROWSER_HEADERS)
        caps = re.findall(
            r"<p[^>]*class=\"b_lineclamp[^\"]*\"[^>]*>(.*?)</p>", html)
        paras = []
        for c in caps[:3]:
            t = re.sub(r"<[^>]+>", "", c).strip()
            t = t.replace("&ensp;", " ").replace("&#0183;", "·")
            t = re.sub(r"^\d{4}年\d+月\d+日\s*", "", t)
            if len(t) >= 30:
                paras.append(t)
        body = "\n".join(paras)[:1200]
        if paras:
            return body
    except Exception:  # noqa: BLE001
        pass
    return ""


def learn_topic(
    memory: Any,
    topic: str,
    *,
    executor: Any = None,
    now: float | None = None,
) -> dict[str, Any]:
    """同步学一个主题（小入口，方便测试）。返回 {ok, topic, wrote, reason}。

    executor 缺省时现场起一个（只读工具不需要审批）：搜索失败返回 ok=False，
    绝不抛异常——学习是加分项。
    """
    t = time.time() if now is None else float(now)
    topic = str(topic or "").strip()[:60]
    if not topic:
        return {"ok": False, "reason": "empty_topic"}
    if not _topic_fresh(topic, t):
        return {"ok": False, "reason": "recently_learned"}
    if not _rate_ok(t):
        return {"ok": False, "reason": "hourly_cap"}
    if executor is None:
        try:
            from aiji.left_hand.actions.executor import ActionExecutor
            executor = ActionExecutor(memory.settings.action_root
                                       if hasattr(memory, "settings") else ".")
        except Exception:  # noqa: BLE001
            return {"ok": False, "reason": "no_executor"}
    try:
        material = _search_web(topic, executor)
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "search_failed"}
    if not material:
        return {"ok": False, "reason": "empty_result"}
    # 深读第一条结果（加分项：失败静默退回标题串）
    deep = ""
    try:
        deep = _deep_read(topic, executor, material)
    except Exception:  # noqa: BLE001
        deep = ""
    if deep:
        material = deep
    else:
        # 退回标题串（老行为）：从 "1. 标题\n   url" 里只留标题
        titles = [re.sub(r"^\d+\.\s*", "", ln.strip())
                  for ln in material.splitlines()
                  if ln.strip() and not ln.strip().startswith("http")][:4]
        material = "；".join(titles)
    try:
        text = f"我查过「{topic}」：{material[:400]}"
        memory.ingest(
            _LEARN_IDENT, kind="text", ref=f"learn:{topic}",
            text=text, session="learn",
            extra={"source": "curiosity", "learn": True, "topic": topic},
        )
        if hasattr(memory, "being_record_event"):
            memory.being_record_event(
                "curiosity", f"自己搜了一下「{topic}」，记住了几条",
                valence_delta=0.6, energy_delta=-0.4)
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "write_failed"}
    return {"ok": True, "topic": topic, "wrote": True}


def maybe_learn_async(memory: Any, query: str, reply_text: str,
                      *, executor: Any = None) -> bool:
    """回复后调用：该学就异步学（不阻塞回复链路）。返回是否安排了学习。"""
    topic = wants_to_learn(reply_text, query)
    if not topic:
        return False

    def _run() -> None:
        try:
            learn_topic(memory, topic, executor=executor)
        except Exception:  # noqa: BLE001 - 学习失败不影响任何主链路
            pass

    threading.Thread(target=_run, name="aiji-curiosity", daemon=True).start()
    return True


__all__ = ["extract_unknown_topic", "wants_to_learn", "learn_topic",
           "maybe_learn_async"]
