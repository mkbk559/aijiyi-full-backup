"""谁是谁：把"名字"讲清楚，免得它把别人的昵称当成自己。

真机证据（2026-11-05 夜，QQ 群）：
  对方「那不就是你嘛」→ 它「我是玛卡巴卡呀，大肥鱼是特大肥鱼那个人啦，别搞混了嘛」
  对方「酸菜鱼还挺会装」→ 它「装什么装呀，本来说的就是我自己的名字嘛」

根因：`玛卡巴卡`（抖音昵称）、`大肥鱼`（声线名）、`特大肥鱼`（群友）、`小肥鱼`（旧自称）
在上下文里混着出现，模型只能瞎猜。这里给一份显式名单注入提示词。
"""
from __future__ import annotations

import json
from pathlib import Path

NAMES_PATH = Path(__file__).resolve().parents[2] / "data" / "persona" / "names.json"

DEFAULT = {
    "self": ["玛卡巴卡", "玛卡巴卡的小推车"],
    "not_me": ["大肥鱼", "特大肥鱼", "小肥鱼", "酸菜鱼"],
    "note": "「大肥鱼」是声线文件的名字，不是你的名字；「特大肥鱼」「悲伤kitty猫」等是群友。",
}


def load(path: Path | str = NAMES_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return dict(DEFAULT)
    merged = dict(DEFAULT)
    merged.update({k: v for k, v in data.items() if v})
    return merged


def names_line(path: Path | str = NAMES_PATH) -> str:
    """给提示词用的一段"名字说明"；没有配置就返回空串（不硬塞）。"""
    data = load(path)
    self_names = "、".join(data.get("self") or [])
    others = "、".join(data.get("not_me") or [])
    if not self_names:
        return ""
    line = f"\n\n## 名字\n你叫 {self_names}。只有这些是你。"
    if others:
        line += f"\n这些**不是你**：{others}——别人提到它们时别往自己身上认，也别纠正确认谁是谁。"
    note = str(data.get("note") or "").strip()
    if note:
        line += f"\n{note}"
    return line
