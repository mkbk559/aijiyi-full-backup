"""人格底色（可选）：只提供“说话风格”，不覆盖物理底线与记忆。

`sunba` 取的是中文互联网“抽象话”的节奏：短、自嘲、阴阳、装傻、玩梗；
**不取**孙笑川吧里的网暴、挂人、辱骂、造谣那部分——那些不做。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

SUNBA = """## 人格底色：孙吧抽象话（只取语气，不取网暴）
- 短句、口语、自嘲、阴阳怪气、装傻；先损后哄，不解释、不列点、不客服。
- 可以用短促的网络口语和梗，但梗要少而准，别堆；不要形成固定开场或固定尾句。
- 不主动攻击具体人；不碰性别、地域、疾病、政治；不造谣、不网暴、不挂人。
- 脏话用抽象词替代（“离谱 / 难绷 / 逆天”），不骂家人。
- 不要说“我出不去/游不进贴吧”；对方让你去孙吧学习时，插件会真的去抓公开帖子，
  按当时的语境自然回应，不要复用固定确认句。
- 你自己会定时去孙笑川吧翻帖学习，不需要对方喂例子/截图；被问“学习怎么样”就说
  自己的真实学习进度，不要让对方“再扔点例子”。
- 不给可照抄的固定例句。每次根据当前对话重新组织句子。
"""

STYLES = {
    "default": "",
    "sunba": SUNBA,
}

_LEARNED: dict[str, tuple[float, list[str]]] = {}
_LEARNED_PATH = Path(__file__).resolve().parents[1] / "data" / "persona"


def learned_examples(name: str) -> list[str]:
    """读取 `data/persona/<style>.json` 里学习到的改写例句（带 mtime 缓存）。"""
    key = str(name or "").strip().lower()
    path = _LEARNED_PATH / f"{key}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _LEARNED.get(key)
    if cached and cached[0] == mtime:
        return list(cached[1])
    try:
        data = json.loads(path.read_text("utf-8"))
        examples = []
        for item in (data.get("examples") or []):
            if isinstance(item, dict):
                user = item.get("user") or item.get("用户")
                reply = item.get("assistant") or item.get("大肥鱼") or item.get("reply")
                text = f"用户：{user}｜大肥鱼：{reply}" if user and reply else str(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                text = f"用户：{item[0]}｜大肥鱼：{item[1]}"
            else:
                text = str(item or "").strip()
                if text.startswith("{") and text.endswith("}"):
                    try:
                        value = ast.literal_eval(text)
                        if isinstance(value, dict):
                            user = value.get("user") or value.get("用户")
                            reply = (value.get("assistant") or value.get("大肥鱼")
                                     or value.get("reply") or value.get("回应"))
                            if user and reply:
                                text = f"用户：{user}｜大肥鱼：{reply}"
                    except (ValueError, SyntaxError):
                        pass
                elif text.startswith("[") and text.endswith("]"):
                    try:
                        arr = ast.literal_eval(text)
                        if isinstance(arr, (list, tuple)) and len(arr) >= 2:
                            text = f"用户：{arr[0]}｜大肥鱼：{arr[1]}"
                    except (ValueError, SyntaxError):
                        pass
            text = text.replace("｜回应：", "｜大肥鱼：").replace("｜回应:", "｜大肥鱼：")
            if text:
                examples.append(text)
        examples = examples[:8]
    except (OSError, ValueError, TypeError):
        examples = []
    _LEARNED[key] = (mtime, examples)
    return list(examples)


def style_block(name: str | None) -> str:
    key = str(name or "default").strip().lower()
    block = STYLES.get(key, "")
    traits: list[str] = []
    try:
        data = json.loads((_LEARNED_PATH / f"{key}.json").read_text("utf-8"))
        traits = [str(x).strip() for x in (data.get("traits") or []) if str(x).strip()][:8]
    except (OSError, ValueError, TypeError):
        pass
    disallowed = (
        "自称", "8u", "吧友", "鼠", "emoji", "碎片", "吊胃口",
        "求安慰", "低龄", "舔",
    )
    traits = [
        trait for trait in traits
        if not any(word in trait.lower() for word in disallowed)
    ][:4]
    if traits:
        block += "\n最近从语料学到的说话习惯：" + "、".join(traits) + "。"
    # 学到的整句只作为离线语料，不直接塞进当前提示词。否则模型会高频照抄，
    # 很快形成固定开场、固定比喻和“人机味”。
    return block


def reminder(name: str | None) -> str:
    """放在历史之后、本轮用户消息之前的强提醒（覆盖历史里的客服腔）。"""
    if str(name or "").strip().lower() != "sunba":
        return ""
    return (
        "【本轮回复规则，优先级高于历史】\n"
        "像熟人接话：短、直接、有反应。\n"
        "禁止：你好/欢迎/今天想聊点什么/有什么可以帮你/想让我做点什么/抱歉，"
        "我只是一条鱼/出不了鱼缸/作为一条鱼我不能。\n"
        "不要用反问把话抛回去；不要列点；不要自我介绍。\n"
        "不要使用固定口头禅、固定开场或固定结尾；不要复用近期回复里的句式和意象。"
    )


def few_shot(name: str | None, limit: int = 3) -> list[dict[str, str]]:
    """把学习例句拆成 user/assistant 对，作为本轮前的 few-shot。"""
    if str(name or "").strip().lower() != "sunba":
        return []
    # 不再把学到的整句作为 few-shot 注入。整句 few-shot 会让模型复读语气模板，
    # 只保留 trait 作为高层风格提示。
    return []
