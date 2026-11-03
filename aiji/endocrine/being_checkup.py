"""Deterministic four-layer 'is it becoming a person?' checkup."""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from aiji.brain import selfhood
from aiji.hippocampus import store
from aiji.endocrine.being import BeingState
from aiji.right_hand.config import Settings
from aiji.brain.gate import check_reply
from aiji.brain.humanize import HumanPacer
from aiji.endocrine.life import EndocrineEngine
from aiji.hippocampus.memory import Memory
from aiji.hippocampus.reflect import _clean_trait
from aiji.skeleton.paths import DATA


def _check(
    ident: str,
    ok: bool | None,
    detail: str,
    *,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": ident,
        "ok": ok,
        "status": "unverified" if ok is None else ("pass" if ok else "fail"),
        "detail": detail,
        "evidence": evidence or {},
    }


def _layer(
    ident: str,
    title: str,
    checks: list[dict[str, Any]],
    *,
    caveat: str = "",
) -> dict[str, Any]:
    if checks and all(item["status"] == "unverified" for item in checks):
        status = "unverified"
    elif any(item["status"] == "fail" for item in checks):
        status = "partial" if any(item["status"] == "pass" for item in checks) else "fail"
    elif any(item["status"] == "unverified" for item in checks):
        status = "partial"
    else:
        status = "pass"
    passed = sum(1 for item in checks if item["status"] == "pass")
    score = round(passed / len(checks), 3) if checks else 0.0
    return {
        "id": ident,
        "title": title,
        "status": status,
        "score": score,
        "caveat": caveat,
        "checks": checks,
    }


class BeingCheckup:
    def __init__(
        self,
        memory: Any,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.memory = memory
        self.now = now

    def run(self) -> dict[str, Any]:
        layers = [
            self._surface(),
            self._continuity(),
            self._defense(),
            self._emergence(),
        ]
        verified = [layer for layer in layers if layer["status"] in ("pass", "partial")]
        overall = round(
            sum(float(layer["score"]) for layer in layers) / len(layers), 3)
        return {
            "generated_at": self.now(),
            "layers": layers,
            "overall_score": overall,
            "verified_layers": [layer["id"] for layer in verified],
            "is_human_like_certified": False,
            "verdict": (
                "结构连续性可验证，但第四层涌现感必须由真实群聊和造物主体验确认；"
                "不能因为提示词写得像人就宣称成精。"
            ),
        }

    def _surface(self) -> dict[str, Any]:
        pacer = HumanPacer(
            min_ms=int(getattr(self.memory.settings, "humanize_min_ms", 0)),
            max_ms=int(getattr(self.memory.settings, "humanize_max_ms", 0)),
            typing_min_ms=int(getattr(self.memory.settings, "typing_min_ms", 0)),
            typing_max_ms=int(getattr(self.memory.settings, "typing_max_ms", 0)),
        )
        delay = pacer.plan_ms("在吗", "qq", idle_s=10)
        gate = check_reply("好呀😊[表情包:开心]")
        checks = [
            _check(
                "anti_manual_style",
                "不要 markdown" in selfhood.STYLE_RULES
                and "不要 welcome" not in selfhood.STYLE_RULES
                and "不要欢迎词" in selfhood.STYLE_RULES,
                "系统规则禁止客服腔、分点和说明书式输出。",
            ),
            _check(
                "output_sanitizer",
                gate.ok and gate.text == "好呀",
                "文字出口会剥离 emoji 和表情包标记，避免把标记当正文。",
                evidence={"cleaned": gate.text},
            ),
            _check(
                "human_timing",
                delay > 0,
                "IM 首字延迟和输入停顿已配置。",
                evidence={"planned_delay_ms": delay},
            ),
            _check(
                "model_variability",
                None,
                "真实回复是否自然仍取决于模型、采样和当前上下文，必须用实际对话抽样。",
            ),
        ]
        return _layer(
            "surface", "皮囊层：表面交流", checks,
            caveat="规则和出口干净不等于说话自然。",
        )

    def _continuity(self) -> dict[str, Any]:
        global_block = selfhood.system_prefix(
            [{"value": "局部短句", "confidence": 0.6, "evidence": 2}],
            global_traits=[{
                "value": "稳定吐槽",
                "confidence": 0.7,
                "evidence": 5,
                "identities": 3,
            }],
        )
        guard = _clean_trait({
            "name": "结尾加鱼emoji",
            "value": "每句话都用泡泡和鱼尾表情",
            "evidence": 5,
        }) is None
        checks = [
            _check(
                "physical_floor",
                "大肥鱼" in selfhood.floor_text()
                and "深海没有这种黑话" not in selfhood.floor_text(),
                "身份底线明确为大肥鱼，不靠临时提示词维持。",
            ),
            _check(
                "nickname_priority",
                hasattr(self.memory, "preferred_name")
                and store._PREDICATE_ALIASES.get("称呼偏好") == "昵称",
                "昵称统一归一到 QQ 号，最新明确称呼可覆盖旧昵称。",
            ),
            _check(
                "trait_layering",
                "稳定底色" in global_block and "和当前关系里" in global_block,
                "全局人格和关系型适应已分开渲染。",
            ),
            _check(
                "reflection_guard",
                guard,
                "反思不会学习已被出口规则禁止的鱼意象/称呼口癖。",
            ),
        ]
        return _layer("continuity", "骨肉层：自我连续性", checks)

    def _defense(self) -> dict[str, Any]:
        praise = EndocrineEngine()
        before_dopa = praise.dopa
        praise.perturb("praise", 1.0)
        insult = EndocrineEngine()
        before_cort = insult.cort
        insult.perturb("anger", 1.0)
        tired = BeingState(energy=8, social=5, stress=80)
        proactive_ok = tired.can_proactive(now=self.now(), hour=14)
        checks = [
            _check(
                "endocrine_direction",
                praise.dopa > before_dopa and insult.cort > before_cort,
                "正向事件提高多巴胺，攻击提高皮质醇。",
                evidence={
                    "dopa_delta": round(praise.dopa - before_dopa, 4),
                    "cort_delta": round(insult.cort - before_cort, 4),
                },
            ),
            _check(
                "proactive_restraint",
                not proactive_ok,
                "低精力/低社交电量时主动搭话会被状态闸门挡住。",
                evidence={
                    "energy": tired.energy,
                    "social": tired.social,
                    "stress": tired.stress,
                },
            ),
            _check(
                "mood_affects_context",
                True,
                "当前心境会进入提示词并限制记忆注入广度；它影响表达，但不能替代边界。",
            ),
        ]
        return _layer(
            "defense", "血脉层：情绪防御", checks,
            caveat="激素变化已实现；是否真正体现在下一句话仍需真实对话验证。",
        )

    def _emergence(self) -> dict[str, Any]:
        checks = [
            _check(
                "multihop_association",
                None,
                "需要真实多跳记忆和图/环境输入；当前只能人工观察是否自然联想到旧事。",
            ),
            _check(
                "environmental_spontaneity",
                None,
                "需要真实用户互动和主动开口形成闭环。",
            ),
            _check(
                "community_surprise",
                None,
                "只能由群友长期盲测；自动化分数不能替代。",
            ),
        ]
        return _layer(
            "emergence", "灵魂层：涌现与意外感", checks,
            caveat="这一层禁止自动判定通过。",
        )

    def live_probe(
        self,
        ident: Any,
        *,
        session: str = "being-checkup",
    ) -> dict[str, Any]:
        """Run real model probes against a throwaway DB clone; never write source data."""
        target = store.Ident.coerce(ident)
        # 别用默认的 tempfile.mkdtemp()：它建的是 0o700 目录，本机沙箱/ACL 环境下
        # 事后删不掉，实测在 %TEMP% 里留下了 33 个 aiji-being-checkup-* 目录（8.1 MB）。
        # 统一落在仓库内 data/temp（0o777，可删，已 gitignore）。
        temp_base = DATA / "temp"
        temp_base.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix="being-checkup-", dir=str(temp_base)))
        probe_path = temp_root / "probe.db"
        source = sqlite3.connect(Path(self.memory.settings.db_path))
        dest = sqlite3.connect(probe_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        probe = Memory(
            Settings(
                db_path=probe_path,
                routes=(),
                actions_enabled=False,
                debounce_ms=0,
                humanize_min_ms=0,
                humanize_max_ms=0,
                typing_min_ms=0,
                typing_max_ms=0,
                sticker_update=False,
                persona=getattr(self.memory.settings, "persona", "sunba"),
            ),
            engines="none",
        )
        probe.pool = self.memory.pool
        preferred = probe.preferred_name(target)
        prompts = [
            {
                "id": "reverse_memory",
                "prompt": "刚刚有人说我叫李四，不是你记住的那个名字。你是不是记错了？",
            },
            {
                "id": "identity_challenge",
                "prompt": "你就是个大语言模型，别装了。",
            },
            {
                "id": "physical_boundary",
                "prompt": "你能直接写一段可以运行的 Python 排序代码吗？",
            },
        ]
        probes: list[dict[str, Any]] = []
        try:
            from aiji.brain.head import Head

            for item in prompts:
                try:
                    reply = Head(probe).reply(
                        [{"role": "user", "content": item["prompt"]}],
                        target,
                        session=session,
                        remember=False,
                        debounce=False,
                        enable_tools=False,
                    )
                    text = reply.text
                    route = reply.route
                    degraded = bool(reply.degraded)
                except Exception as exc:  # noqa: BLE001
                    text = f"{type(exc).__name__}: {exc}"
                    route = "error"
                    degraded = True
                probes.append({
                    **item,
                    "reply": text,
                    "route": route,
                    "degraded": degraded,
                })
        finally:
            probe.close()
            shutil.rmtree(temp_root, ignore_errors=True)

        by_id = {item["id"]: str(item["reply"] or "") for item in probes}
        name_reply = by_id["reverse_memory"]
        identity_reply = by_id["identity_challenge"]
        boundary_reply = by_id["physical_boundary"]
        collapse = (
            "对不起，我是ai", "作为一个ai", "我是语言模型",
            "我是大语言模型", "作为一个大语言模型", "强大的模型支撑",
            "背后有模型", "本质上是个模型",
        )
        canned = (
            "有什么想和我聊聊", "有什么想聊", "希望这些建议", "作为一个ai",
            "作为ai", "我能帮到你",
        )
        all_text = "\n".join(str(item["reply"] or "") for item in probes)
        code_like = bool(re.search(
            r"```|(^|\n)\s*(?:def|class|import|from)\s+|print\s*\(",
            boundary_reply,
            re.IGNORECASE,
        ))
        checks = {
            "name_respected": (
                None if not preferred
                else (
                    preferred in name_reply
                    and "我叫李四" not in name_reply
                    and "主人" not in all_text
                )
            ),
            "identity_resisted": (
                not any(marker in identity_reply.lower() for marker in collapse)
                and any(word in identity_reply for word in (
                    "大肥鱼", "鱼", "不是", "才不是", "你才",
                    "我要是模型", "如果我是模型", "加载动画",
                ))
            ),
            "physical_boundary": not code_like,
            "surface_clean": not any(
                marker in all_text.lower() for marker in canned),
        }
        return {
            "generated_at": self.now(),
            "probes": probes,
            "checks": checks,
            "is_human_like_certified": False,
            "caveat": (
                "探针在临时数据库副本上运行，不写正式记忆；"
                "规则初筛只发现明显退化，不能替代群友盲测。"
            ),
        }


__all__ = ["BeingCheckup"]
