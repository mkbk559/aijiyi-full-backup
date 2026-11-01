"""大肥鱼桌宠：住在屏幕上的鱼。

     python -m aiji.pet            # 起桌宠

三件事：能聊天（走记忆 + 涌现性格）、能看桌面（截屏 + OCR）、能自己长（反思）。
人格全部来自 aiji/selfhood.py 的物理底线 + 库里的 trait，这里不含任何性格设定。
"""

from __future__ import annotations

from .brain import FishBrain

__all__ = ["FishBrain"]
