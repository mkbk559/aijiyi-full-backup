"""无状态对话头：把外部消息编排成一次带记忆与人格约束的模型调用。

它不持有会话状态，也不绑定任何 AI 平台。上游是用户已有的 OpenAI 兼容
积分网关，下游可以是 IDE、IM 网关或任何能填自定义 API 地址的客户端。
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from aiji.brain import day as day_mod
from aiji.brain import names as names_mod
from aiji.brain import growth as growth_mod
from aiji.brain import persona as persona_mod
from aiji.brain import role_boundary
from aiji.brain import selfhood
from aiji.left_hand.actions import OPENAI_TOOLS
from aiji.brain import gate as gate_mod
from aiji.brain.gate import check_reply, fit_reply, reply_char_limit
from aiji.right_hand import params
from aiji.brain.humanize import IM_PLATFORMS
from aiji.endocrine.life import infer_event
from aiji.brain.pool import PoolExhausted
from aiji.hippocampus.store import Ident

logger = logging.getLogger(__name__)

FALLBACK_TEXT = "刚才没接上，等我一下。"


def _dedupe_history(messages: list[dict[str, Any]], *, window: int = 20) -> list[dict[str, Any]]:
    """保序去重：同一 (role, content) 在窗口内只留第一条。

    镜像会把同一句话写两遍（平台身份一条 + 共享一条）；模型看到两遍，
    就会煞有介事地说"你在学我说话、咱俩的词对半分了"，然后一直纠缠。
    只动喂给模型的副本，不改数据库。
    """
    seen: dict[tuple[str, str], int] = {}
    out: list[dict[str, Any]] = []
    for msg in messages or []:
        role = str(msg.get("role") or "")
        text = str(msg.get("content") or "")
        if role == "system":
            out.append(msg)
            continue
        key = (role, text)
        last = seen.get(key)
        if last is not None and len(out) - last <= window:
            continue
        seen[key] = len(out)
        out.append(msg)
    return out


@dataclass(slots=True)
class HeadReply:
    text: str
    query: str
    session: str
    injection_id: int = 0
    used_ids: list[int] | None = None
    route: str = ""
    model_latency_ms: float = 0.0
    human_delay_ms: float = 0.0
    took_ms: float = 0.0
    finish_reason: str = "stop"
    reason: str = ""
    degraded: bool = False
    tool_traces: list[dict[str, Any]] | None = None
    life: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.finish_reason == "stop"


def _content_text(content: Any) -> str:
    """Return the textual query while retaining a marker for visual input."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text") and part.get("text"):
                parts.append(str(part["text"]))
            elif part.get("type") in ("image_url", "input_image"):
                parts.append("[图片]")
        return "\n".join(parts)
    return ""


def normalize_messages(messages: Any) -> list[dict[str, Any]]:
    """Keep supported OpenAI text and image parts in their original order."""
    if not isinstance(messages, list):
        raise ValueError("messages_required")
    out: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in ("system", "user", "assistant"):
            continue
        content = item.get("content")
        text = _content_text(content)
        if text.strip():
            if isinstance(content, list):
                supported = [
                    dict(part) for part in content
                    if isinstance(part, dict) and part.get("type") in (
                        "text", "input_text", "image_url", "input_image")
                ]
                out.append({"role": role, "content": supported})
            else:
                out.append({"role": role, "content": text})
    if not any(item["role"] == "user" for item in out):
        raise ValueError("user_message_required")
    return out


class Head:
    def __init__(self, memory: Any):
        self.memory = memory

    def reply(
        self,
        messages: Any,
        ident: Any,
        *,
        session: str = "default",
        timeout_s: float = 60.0,
        remember: bool = True,
        debounce: bool = True,
        system_suffix: str = "",
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        enable_tools: bool = True,
        persona: str | None = None,
        extra: dict | None = None,
        proactive: bool = False,
    ) -> HeadReply:
        ident = Ident.coerce(ident)
        session = str(session or "default")
        presence = getattr(self.memory, "presence", None)
        if presence is not None:
            try:
                preview = next(
                    _content_text(item["content"])
                    for item in reversed(normalize_messages(messages))
                    if item["role"] == "user"
                )
            except (ValueError, StopIteration):
                preview = ""
            presence.publish({
                "state": "thinking",
                "mood": "专注",
                "platform": ident.platform,
                "user": ident.platform_user_id,
                "scope": str(ident.scope or "default"),
                "session": session,
                "preview": preview[:80],
            })
        coordinator = getattr(self.memory, "debouncer", None)
        if not debounce or coordinator is None or coordinator.window_s <= 0:
            return self._reply_once(
                messages, ident, session=session,
                timeout_s=timeout_s, remember=remember,
                system_suffix=system_suffix,
                temperature=temperature, top_p=top_p, max_tokens=max_tokens,
                enable_tools=enable_tools, persona=persona, extra=extra,
                proactive=proactive,
            )
        key = (ident.platform, ident.platform_user_id,
               str(ident.scope or "default"), session)
        return coordinator.call(
            key, messages,
            lambda merged: self._reply_once(
                merged, ident, session=session,
                timeout_s=timeout_s, remember=remember,
                system_suffix=system_suffix,
                temperature=temperature, top_p=top_p, max_tokens=max_tokens,
                enable_tools=enable_tools, persona=persona, extra=extra,
                proactive=proactive,
            ),
        )

    def _reply_once(
        self,
        messages: Any,
        ident: Ident,
        *,
        session: str,
        timeout_s: float,
        remember: bool,
        system_suffix: str,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        enable_tools: bool = True,
        persona: str | None = None,
        extra: dict | None = None,
        proactive: bool = False,
    ) -> HeadReply:
        t0 = time.perf_counter()
        normalized = normalize_messages(messages)
        query = next(_content_text(item["content"]) for item in reversed(normalized)
                     if item["role"] == "user")
        physical_action = (
            str(ident.platform).lower() in IM_PLATFORMS
            and role_boundary.is_physical_action_request(query)
        )
        presence = getattr(self.memory, "presence", None)
        target = {
            "platform": ident.platform,
            "user": ident.platform_user_id,
            "scope": str(ident.scope or "default"),
            "session": session,
        }

        now = time.time()
        being_before = (
            self.memory.being_snapshot(now=now)
            if hasattr(self.memory, "being_snapshot") else {}
        )
        idle_s = float(being_before.get("idle_s", 0.0) or 0.0)
        if proactive and being_before and not being_before.get("proactive_ok", True):
            return HeadReply(
                text="",
                query=query,
                session=session,
                finish_reason="skip",
                reason="proactive_state_blocked",
                life=being_before,
            )
        # 主动配额 = 反代积分（2026-11-02）：额度不够就别开口，省下来的留给用户回话。
        if proactive and hasattr(self.memory, "proactive_quota_ok") \
                and not self.memory.proactive_quota_ok(ident, now=now):
            return HeadReply(
                text="",
                query=query,
                session=session,
                finish_reason="skip",
                reason="proactive_quota",
                life=being_before,
            )
        if proactive:
            life_snapshot = (
                self.memory.life_snapshot(ident, now=now)
                if hasattr(self.memory, "life_snapshot") else None
            )
            being_snapshot = being_before
        else:
            life_snapshot = self._life_event(query, ident)
            being_snapshot = (
                self.memory.being_observe(query, now=now)
                if hasattr(self.memory, "being_observe") else {}
            )
        being_block = (
            self.memory.being_prompt(being_snapshot, proactive=proactive)
            if being_snapshot and hasattr(self.memory, "being_prompt") else ""
        )
        base_items = int(self.memory.settings.top_k)
        if life_snapshot:
            factor = float(life_snapshot.get("endo_factor", 1.0))
            base_items = max(3, min(12, round(base_items * max(0.65, factor))))
        injection = self.memory.inject(
            query, ident, session=session,
            max_items=base_items,
            mode="chat",
        )
        persona_key = persona or None   # 旧人格已删除：不指定就按成长阶段
        # 成长引擎 v2：阶段由"学习量（学分）"决定，不看真实时间；
        # 每轮先结算一次增量（内部 60 秒节流），再从阶段渲染人格文案。
        growth_mod.configure(
            age=getattr(self.memory.settings, "persona_age", None),
            stage=getattr(self.memory.settings, "persona_stage", None),
        )
        try:
            growth_mod.current().sync(getattr(self.memory, "db", None))
        except Exception:  # noqa: BLE001 - 结算失败也要能正常聊天
            pass
        persona_growth = growth_mod.current()
        persona_age = persona_growth.age_years()
        persona_stage = persona_growth.stage().id
        persona_key = persona or persona_growth.stage().id
        persona_block = (persona_mod.style_block(persona) if persona
                         else persona_growth.block())
        caller_name = ""
        preferred_name = ""
        if isinstance(extra, dict):
            caller_name = re.sub(
                r"[\x00-\x1f\x7f<>\[\]]+", " ",
                str(extra.get("sender_name") or ""),
            )
            caller_name = " ".join(caller_name.split())[:64]
        if hasattr(self.memory, "preferred_name"):
            preferred_name = str(self.memory.preferred_name(ident) or "").strip()
        caller_lines = ["## 当前对话者", "你的名字是玛卡巴卡。"]
        if caller_name:
            caller_lines.append(
                f"对方的平台昵称是「{caller_name}」。这个昵称只属于对方，"
                "不要把这个名字当成你自己的名字。"
            )
        if preferred_name:
            caller_lines.append(
                f"当前希望被称呼为「{preferred_name}」。"
                "这优先于平台昵称和历史昵称；不要再混用其他称呼。"
                f"你称呼对方为「{preferred_name}」，但你的名字始终是玛卡巴卡；"
                "绝不能把对方的昵称当成你的名字或自称。"
            )
        elif not caller_name:
            caller_lines.append("当前对方没有可用的平台昵称。")
        caller_block = "\n".join(caller_lines)
        system_messages = [{
            "role": "system",
            "content": selfhood.system_prefix(
                self.memory.traits(ident),
                global_traits=(
                    self.memory.global_traits()
                    if hasattr(self.memory, "global_traits") else []
                ),
                persona=persona_key,
                age=persona_age,
                stage=persona_stage,
            )
            + (f"\n\n{persona_block}" if persona_block else "")
            + (f"\n\n{system_suffix}" if system_suffix else "")
            + self._life_prompt(life_snapshot)
            + (f"\n\n{being_block}" if being_block else "")
            + (self._day_line(persona_stage))
            + names_mod.names_line(),
        }]
        if injection.text.strip():
            system_messages.append({"role": "system", "content": injection.text})
        # 当前昵称/关系标签放在记忆之后，避免旧注入把用户最新纠正覆盖掉。
        system_messages.append({"role": "system", "content": caller_block})
        if physical_action:
            system_messages.append({
                "role": "system",
                "content": role_boundary.prompt_block(),
            })
        # 人格小样本只对 child 提供（sunba 明确不注入整句，避免复读语气模板）：
        # 放在系统消息之后、真实对话之前，只锚"声音"，内容仍按当轮生成。
        persona_shots = (persona_mod.few_shot(persona) if persona
                         else persona_growth.few_shot())
        # 见 _dedupe_history 的注释：镜像重复会让模型误判"对方在重复我"
        normalized = _dedupe_history(normalized)
        assembled = system_messages + persona_shots + normalized

        tool_traces: list[dict[str, Any]] = []
        action_executor = getattr(self.memory, "action_executor", None)
        tools = OPENAI_TOOLS if (
            enable_tools
            and action_executor is not None
            and bool(getattr(self.memory.settings, "actions_enabled", False))
        ) else None
        max_rounds = max(1, int(getattr(self.memory.settings, "action_max_rounds", 4)))
        model_messages = list(assembled)
        chat_params = inspect.signature(self.memory.pool.chat).parameters.values()
        supports_tools = any(
            item.name == "tools" or item.kind == inspect.Parameter.VAR_KEYWORD
            for item in chat_params
        )
        # 池子不一定收 max_tokens（测试里的假池子就不收）：收才塞，别把 fixture 打崩
        supports_max_tokens = any(
            item.name == "max_tokens" or item.kind == inspect.Parameter.VAR_KEYWORD
            for item in chat_params
        )
        try:
            for round_no in range(max_rounds + 1):
                kwargs = {"timeout_s": timeout_s}
                if temperature is not None:
                    kwargs["temperature"] = temperature
                if top_p is not None:
                    kwargs["top_p"] = top_p
                # 硬上限：不给就用默认上限，给了也不许超过 params.MAX_REPLY_TOKENS
                if supports_max_tokens:
                    kwargs["max_tokens"] = params.MAX_REPLY_TOKENS if max_tokens is None else min(
                        int(max_tokens), params.MAX_REPLY_TOKENS)
                if tools and supports_tools:
                    kwargs["tools"] = tools
                result = self.memory.pool.chat(model_messages, **kwargs)
                calls = list(getattr(result, "tool_calls", []) or [])
                if not calls or action_executor is None:
                    break
                if round_no >= max_rounds:
                    result = type("Result", (), {
                        "text": "工具执行轮次已达上限，我先停下来等你检查。",
                        "route": getattr(result, "route", ""),
                        "latency_ms": getattr(result, "latency_ms", 0.0),
                        "tool_calls": [],
                    })()
                    break
                model_messages.append({
                    "role": "assistant",
                    "content": str(getattr(result, "text", "") or ""),
                    "tool_calls": [{
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    } for call in calls],
                })
                if presence is not None:
                    presence.publish({
                        "state": "acting", "mood": "专注", **target,
                        "preview": "、".join(call.name for call in calls[:3]),
                    })
                for call in calls:
                    tool_result = action_executor.execute(call)
                    payload = tool_result.output if tool_result.ok else (
                        f"ERROR: {tool_result.error}"
                    )
                    model_messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": payload[:30_000],
                    })
                    tool_traces.append({
                        "id": call.id,
                        "name": call.name,
                        "ok": tool_result.ok,
                        "approved": tool_result.approved,
                        "risk": tool_result.risk,
                        "error": tool_result.error,
                    })
        except PoolExhausted as exc:
            if life_snapshot is not None:
                life_snapshot = self.memory.life_event(ident, "stress", 0.6)
            if proactive:
                return HeadReply(
                    text="", query=query, session=session,
                    finish_reason="skip", reason=str(exc),
                    life=being_snapshot,
                )
            if (
                hasattr(self.memory, "allow_fallback")
                and not self.memory.allow_fallback(ident, session)
            ):
                return HeadReply(
                    text="",
                    query=query,
                    session=session,
                    finish_reason="skip",
                    reason="fallback_cooldown",
                    life=life_snapshot,
                )
            pacer = getattr(self.memory, "human_pacer", None)
            human_delay_ms = (
                pacer.wait(
                    FALLBACK_TEXT, ident.platform,
                    energy=float(being_snapshot.get("energy", 60)),
                    idle_s=idle_s,
                    urgent=self._is_urgent(query),
                ) if pacer is not None else 0.0
            )
            if remember:
                self.memory.turn(ident, session, "user", query, extra=extra)
            if presence is not None:
                presence.publish({
                    "state": "degraded", "mood": "警觉", **target,
                    "preview": FALLBACK_TEXT,
                    "error": str(exc)[:180],
                })
            return HeadReply(
                text=FALLBACK_TEXT,
                query=query,
                session=session,
                injection_id=injection.injection_id,
                used_ids=[item.id for item in injection.items if item.id],
                route="fallback",
                human_delay_ms=human_delay_ms,
                took_ms=(time.perf_counter() - t0) * 1000,
                reason=str(exc),
                degraded=True,
                tool_traces=tool_traces,
                life=life_snapshot,
            )
        raw = (getattr(result, "text", "") or "").strip()
        if raw and hasattr(self.memory, "clear_fallback"):
            self.memory.clear_fallback(ident, session)
        if self._needs_style_rewrite(raw, query):
            original = raw
            try:
                rewrite_messages = list(model_messages) + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "system",
                        "content": (
                            "重写上一条回复。保留原本意思和态度，删掉固定口头禅、"
                            "固定开场和重复出现的模板意象。不要解释，"
                            "不要提规则，只输出重写后的正文。"
                        ),
                    },
                ]
                rewrite_kwargs = {"timeout_s": timeout_s, "temperature": 1.05}
                if top_p is not None:
                    rewrite_kwargs["top_p"] = top_p
                if supports_max_tokens:
                    rewrite_kwargs["max_tokens"] = params.MAX_REPLY_TOKENS if max_tokens is None else min(
                        int(max_tokens), params.MAX_REPLY_TOKENS)
                rewritten = self.memory.pool.chat(
                    rewrite_messages, **rewrite_kwargs)
                candidate = (getattr(rewritten, "text", "") or "").strip()
                original_numbers = re.findall(r"\d+(?:[.:：/]\d+)*", original)
                candidate_numbers = re.findall(r"\d+(?:[.:：/]\d+)*", candidate)
                if (candidate and not self._needs_style_rewrite(candidate, query)
                        and all(candidate_numbers.count(number) >= original_numbers.count(number)
                                for number in set(original_numbers))):
                    raw = candidate
                    result = rewritten
            except Exception as exc:  # noqa: BLE001 - keep original rather than fail chat
                logger.warning("style rewrite failed: %s", exc)
            if self._needs_style_rewrite(raw, query):
                raw = self._strip_style_fillers(original, query)
        if physical_action and role_boundary.contains_executable_code(raw):
            raw = role_boundary.refusal(query)
        # 身份兜底：人格一旦设定（child/sunba），任何入口都不许自曝"我是 AI/在扮演"。
        # 2026-11-03 实测：只在 qq/wechat 修，API 入口会漏（14 岁档回
        # "我其实是个 AI，在扮演一个叫玛卡巴卡的角色"）。
        if (
            True  # 旧人格已删除：这条对所有身份都适用
            and role_boundary.contains_identity_disclosure(raw)
        ):
            raw = role_boundary.identity_repair(raw)
        if preferred_name and hasattr(self.memory, "stale_nicknames"):
            for stale in self.memory.stale_nicknames(ident):
                if stale and stale in raw:
                    raw = raw.replace(stale, preferred_name)
            raw = re.sub(
                rf"我(?:就)?是(?:你|这个|那个)?\s*{re.escape(preferred_name)}",
                "我是玛卡巴卡",
                raw,
            )
        # 出口硬上限：先按平台裁进字符预算，再交给闸门校验（裁过不再整条丢弃）
        # 小学/初中还在学说话，模型偶尔会甩代码围栏出来，直接剥掉。
        if persona_key == "child" and persona_stage in ("primary", "middle"):
            raw = gate_mod.strip_code_fences(raw)
        raw = fit_reply(raw, reply_char_limit(ident.platform))
        gate = check_reply(raw)
        text = gate.text if gate.ok else "这话我说不出口。"
        pacer = getattr(self.memory, "human_pacer", None)
        human_delay_ms = (
            pacer.wait(
                text, ident.platform,
                energy=float(being_snapshot.get("energy", 60)),
                idle_s=idle_s,
                urgent=self._is_urgent(query),
            ) if pacer is not None else 0.0
        )

        if remember and text:
            self.memory.exchange(ident, session, query, text, extra=extra)
        if proactive and text and hasattr(self.memory, "being_mark_proactive"):
            being_snapshot = self.memory.being_mark_proactive(now=time.time())
        if proactive and text and hasattr(self.memory, "proactive_spend"):
            self.memory.proactive_spend(ident, now=time.time())
        if presence is not None:
            mood = "警觉" if not gate.ok else (
                "愉快" if any(ch in text for ch in "！!～~") else "专注"
            )
            presence.publish({
                "state": "speaking", "mood": mood, **target,
                "preview": text[:80],
                "route": str(getattr(result, "route", "") or ""),
            })

        return HeadReply(
            text=text,
            query=query,
            session=session,
            injection_id=injection.injection_id,
            used_ids=[item.id for item in injection.items if item.id],
            route=str(getattr(result, "route", "") or ""),
            model_latency_ms=float(getattr(result, "latency_ms", 0.0) or 0.0),
            human_delay_ms=human_delay_ms,
            took_ms=(time.perf_counter() - t0) * 1000,
            finish_reason="stop" if gate.ok else "content_filter",
            reason=gate.reason,
            tool_traces=tool_traces,
            life=life_snapshot,
        )

    def _day_line(self, stage_id: str = "") -> str:
        """把"现在几点、这会儿在干嘛"写进提示词——上课就短回，晚上有空多聊。

        作息由 `aiji/brain/day.py` 决定，这里只读不自己算。
        """
        try:
            block = day_mod.current_block(stage_id=stage_id)
        except Exception:  # noqa: BLE001
            return ""
        talk = {"none": "这会儿不方便多聊，回一两句就好",
                "short": "只能短句回", "normal": "有空，可以正常聊"}.get(block.get("talk"), "")
        return (f"\n\n## 现在\n{time.strftime('%H:%M')}，"
                f"你正在：{block.get('what')}（到 {block.get('end')}）。{talk}。")

    @staticmethod
    def _needs_style_rewrite(text: str, query: str) -> bool:
        value = str(text or "").strip()
        source = str(query or "")
        if not value:
            return False
        for phrase in (
            "咋了", "咕噜", "泡泡", "鱼缸", "水里", "小鱼", "本鱼", "鳍", "鳃",
        ):
            if phrase in value and phrase not in source:
                return True
        return False

    @staticmethod
    def _strip_style_fillers(text: str, query: str) -> str:
        value = str(text or "").strip()
        source = str(query or "")
        phrases = (
            "咋了", "咕噜", "泡泡", "鱼缸", "水里", "小鱼", "本鱼", "鳍", "鳃",
        )
        for phrase in phrases:
            if phrase in source:
                continue
            value = re.sub(
                rf"{re.escape(phrase)}[呢呀啊吧~～]*[，,、]?",
                "",
                value,
            )
        value = re.sub(r"[，,]\s*(?=[，,。！？!?])", "", value)
        value = re.sub(r"([。！？!?])\1+", r"\1", value)
        value = re.sub(r"\s{2,}", " ", value)
        return value.strip(" ，,、\r\n")

    def _life_event(self, text: str, ident: Ident) -> dict[str, Any] | None:
        if not hasattr(self.memory, "life_event"):
            return None
        event_type, intensity = infer_event(text)
        return self.memory.life_event(ident, event_type, intensity)

    @staticmethod
    def _is_urgent(text: str) -> bool:
        return bool(re.search(
            r"急|马上|立刻|现在|报警|救命|不舒服|疼|难受|怎么办", text or ""))

    @staticmethod
    def _life_prompt(life: dict[str, Any] | None) -> str:
        if not life:
            return ""
        hormones = life.get("hormones") or {}
        if float(hormones.get("cort", 0)) >= 0.55:
            mood = "有点紧张，先稳住再回答"
        elif float(hormones.get("oxy", 0)) >= 0.65:
            mood = "比较亲近，但仍保持边界"
        elif float(hormones.get("dopa", 0)) >= 0.60:
            mood = "活跃，表达可以更轻快"
        else:
            mood = "平静"
        return f"\n\n## 当前心境\n{mood}。心境只影响表达倾向，不改变物理底线和事实。"


__all__ = ["FALLBACK_TEXT", "Head", "HeadReply", "normalize_messages"]

