"""桌宠的大脑：能把话说明白，也能把"看到的桌面"说出来。

这里**零人格硬编码**：系统提示词只由 `selfhood.system_prefix()` 生成
（物理底线 + 库里涌现的性格）。想让它换性格，去改互动/跑反思，不要改这个文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import selfhood
from ..head import Head
from ..store import Ident
from . import desk, voice

CHAT_RULES = (
    "说话规则：\n"
    "1. 短。桌宠气泡放不下长文，一般 1-3 句，最多 4 句。\n"
    "2. 说人话，不要 markdown、不要列表、不要标题。\n"
    "3. 你只能靠文字说话，没有表情包也没有图片。\n"
    "4. 记不清的事就承认记不清，别编。"
)

NO_BRAIN = "我现在连不上嘴（模型没接上），说不出来话。"


class FishBrain:
    """桌宠的对话与感知。所有方法都不碰 GUI，可以在测试里直接跑。"""

    def __init__(
        self,
        memory: Any,
        ident: Any,
        session: str = "pet",
        *,
        top_k: int | None = None,
    ):
        self.memory = memory
        self.ident = Ident.coerce(ident)
        self.session = session or "pet"
        self.top_k = top_k

    # ---- 提示词 ----

    def system(self) -> str:
        """物理底线 + 当前涌现性格。每次现算，性格变了立刻生效。"""
        traits = self.memory.traits(self.ident)
        return selfhood.system_prefix(traits) + "\n\n" + CHAT_RULES

    def has_brain(self) -> bool:
        pool = getattr(self.memory, "pool", None)
        return bool(getattr(pool, "_states", None))

    # ---- 对话 ----

    def reply(self, text: str, *, remember: bool = True, timeout_s: float = 30.0) -> dict[str, Any]:
        """回一句话。返回 {text, ok, reason, injection_id, took_ms}。"""
        text = (text or "").strip()
        if not text:
            return {"text": "", "ok": False, "reason": "empty", "took_ms": 0.0}
        if not self.has_brain():
            return {"text": NO_BRAIN, "ok": False, "reason": "no_model", "took_ms": 0.0}

        result = Head(self.memory).reply(
            [{"role": "user", "content": text}],
            self.ident,
            session=self.session,
            timeout_s=timeout_s,
            remember=remember,
            system_suffix=CHAT_RULES,
        )
        return {
            "text": result.text,
            "ok": result.ok and not result.degraded,
            "reason": result.reason,
            "injection_id": result.injection_id,
            "took_ms": round(result.took_ms, 1),
        }

    # ---- 看桌面 ----

    def look(self, *, do_ocr: bool = True) -> dict[str, Any]:
        return desk.look(do_ocr=do_ocr)

    def comment_on(self, lk: dict[str, Any], *, ask: str = "", timeout_s: float = 30.0) -> dict[str, Any]:
        """对"看到的桌面"说一句。没模型时如实说没模型。"""
        seen = desk.render(lk)
        if not self.has_brain():
            return {"text": NO_BRAIN + "\n（不过我还是看到了：" + seen[:200] + "）",
                    "ok": False, "reason": "no_model"}
        user = f"我刚看了一眼你的桌面：\n{seen}\n\n"
        user += (ask.strip() or "用一两句话说说你看到了什么、我在干什么。")
        result = Head(self.memory).reply(
            [{"role": "user", "content": user}],
            self.ident,
            session=self.session,
            timeout_s=timeout_s,
            system_suffix=CHAT_RULES,
        )
        return {
            "text": result.text,
            "ok": result.ok and not result.degraded,
            "reason": result.reason,
            "seen": seen,
        }

    # ---- 反思 ----

    def reflect(self, *, force: bool = False) -> dict[str, Any] | None:
        return self.memory.reflect(self.ident, self.session, force=force)

    # ---- 语音对话（说 → 听 → 想 → 说）----

    def voice_reply(
        self, seconds: float = voice.DEFAULT_SECONDS, *, tmp_dir: str | Path = ".tmp",
        speak_back: bool = True,
    ) -> dict[str, Any]:
        """录一句 → 听懂 → 回话 → 念出来。任一步失败都如实说明。"""
        heard = voice.listen(seconds, tmp_dir=tmp_dir)
        if not heard["ok"]:
            return {"heard": "", "reply": "", "spoken": False,
                    "reason": heard["reason"], "confidence": heard["confidence"],
                    "took_ms": heard["took_ms"]}
        r = self.reply(heard["text"])
        spoken = voice.speak(r["text"]) if speak_back else False
        return {"heard": heard["text"], "reply": r["text"], "spoken": spoken,
                "ok": r["ok"], "reason": r["reason"],
                "confidence": heard["confidence"], "took_ms": heard["took_ms"] + r["took_ms"]}


__all__ = ["CHAT_RULES", "FishBrain", "NO_BRAIN"]
