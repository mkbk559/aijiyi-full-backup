"""形象层：直接用插件的鲸鱼娘图集（不再手绘）。

- `state → 轨迹` 的映射集中在这里
- `render()` 取一帧并合成到指定底色（Tk 用 -transparentcolor 抠色，所以底色必须是不透明纯色）
- `orb()` 保留旧接口（悬浮球时代的调用），内部也走图集

想换形象：换 `assets/whale_spritesheet.webp` + 这张映射表即可，代码不动。
"""

from __future__ import annotations

from typing import Any

from . import sprite

# 桌宠状态 → 图集轨迹（插件 9 条轨迹里挑合适的）
STATE_TRACK = {
    "idle": "idle",
    "talk": "waving",
    "listen": "waiting",
    "happy": "jumping",
    "sleep": "review",
    "think": "running",
    "fail": "failed",
}

STATES = tuple(STATE_TRACK)

TRANSPARENT = (1, 2, 3, 255)  # 与 app.TRANSPARENT 同色（#010203）


def track_for(state: str) -> str:
    return STATE_TRACK.get(state, "idle")


def render(state: str = "idle", index: int = 0, height: int = 112,
           bg: tuple[int, int, int, int] = TRANSPARENT):
    """一帧，合成到 bg 上（bg 不透明，Tk 靠 -transparentcolor 抠掉）。"""
    from PIL import Image

    frame = sprite.cell(track_for(state), index, height)
    out = Image.new("RGBA", frame.size, bg)
    out.paste(frame, (0, 0), frame)
    return out


def frame(track: str, index: int, height: int = 112):
    """按轨迹名直接取帧（动画循环用，带 alpha）。"""
    return sprite.cell(track, index, height)


def orb(size: int = 76, state: str = "idle"):
    """旧接口：正方形头像位，现在返回整只鲸鱼娘（按高度缩放）。"""
    return render(state, 0, size)


def draw(w: int = 240, h: int = 260, state: str = "idle"):
    """旧接口：返回立绘（按 h 缩放，宽按比例）。"""
    return render(state, 0, h)


def head_crop(size: int = 64, state: str = "idle"):
    """旧接口：小头像（直接缩到 size 方形）。"""
    return render(state, 0, size)


__all__ = ["STATES", "STATE_TRACK", "draw", "frame", "head_crop", "orb", "render", "track_for"]
