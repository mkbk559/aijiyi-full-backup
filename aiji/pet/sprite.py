"""鲸鱼娘精灵图集（与原插件 @linxin666/dsh-pet 完全一致）。

资产：`assets/whale_spritesheet.webp`（1536×1872，8 列 × 9 行，单元格 192×208）
      `assets/whale_pet.json`（每行帧数 frames + 每条轨迹的逐帧时长 tracks）

跟随的行为契约（照抄插件的 `spritesheet.js` / `PetSprite.js`）：
- 行序固定：0 idle / 1 running-right / 2 running-left / 3 waving /
  4 jumping / 5 failed / 6 waiting / 7 running / 8 review
- 帧推进：累计 elapsed，逐帧减去该帧时长；走完且 loop 则从头，否则停在最后一帧
- trimTrack：轨迹长度被该行真实帧数截断（否则透明格会让宠物"消失"）
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

CELL_W, CELL_H = 192, 208
COLUMNS, ROWS = 8, 9

# 行号 → 轨迹名（固定 9 行契约，与插件一模一样）
ROW_OF_TRACK = {
    "idle": 0,
    "running-right": 1,
    "running-left": 2,
    "waving": 3,
    "jumping": 4,
    "failed": 5,
    "waiting": 6,
    "running": 7,
    "review": 8,
}
TRACK_NAMES = tuple(ROW_OF_TRACK)

# 每行真实帧数（pet.json 的 frames）
FRAMES = (6, 8, 8, 4, 5, 8, 6, 6, 6)

# 逐帧时长（毫秒），与 pet.json 的 tracks 一致
TRACKS: dict[str, dict[str, Any]] = {
    "idle": {"durations": (400, 400, 500, 400, 400, 500), "loop": True},
    "running-right": {"durations": (225,) * 8, "loop": True},
    "running-left": {"durations": (225,) * 8, "loop": True},
    "waving": {"durations": (350,) * 4, "loop": True},
    "jumping": {"durations": (300, 300, 300, 350, 350), "loop": False},
    "failed": {"durations": (450, 450, 450, 500, 550, 600, 450, 450), "loop": False},
    "waiting": {"durations": (450, 450, 500, 450, 450, 500), "loop": True},
    "running": {"durations": (250,) * 6, "loop": True},
    "review": {"durations": (550,) * 6, "loop": True},
}


def asset_dir() -> Path:
    try:
        return Path(resources.files(__package__)) / "assets"
    except Exception:  # noqa: BLE001
        return Path(__file__).parent / "assets"


def load_manifest() -> dict[str, Any]:
    """读 pet.json（若有）——资产里的节奏表是权威，缺了用内置常量。"""
    p = asset_dir() / "whale_pet.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


@lru_cache(maxsize=1)
def atlas() -> Any:
    from PIL import Image

    return Image.open(asset_dir() / "whale_spritesheet.webp").convert("RGBA")


def trim_track(track: dict[str, Any], frame_count: int) -> dict[str, Any]:
    """照抄 trimTrack：长度被行真实帧数截断，至少 1 帧。"""
    n = max(1, min(frame_count, len(track["durations"])))
    return {"durations": tuple(track["durations"])[:n], "loop": track.get("loop", True)}


@lru_cache(maxsize=256)
def cell(track: str, index: int, size: int = 0) -> Any:
    """取某轨迹第 index 帧（可选等比缩放到高度 size），返回 RGBA。"""
    from PIL import Image

    row = ROW_OF_TRACK.get(track, 0)
    n = max(1, min(FRAMES[row], len(TRACKS.get(track, TRACKS["idle"])["durations"])))
    col = index % n
    img = atlas().crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
    if size and size != CELL_H:
        scale = size / CELL_H
        img = img.resize((max(1, round(CELL_W * scale)), max(1, round(size))), Image.LANCZOS)
    return img


class Animator:
    """帧推进器：与插件的 requestAnimationFrame 循环同语义，只是由调用方喂时间。

    用法：
        a = Animator("idle")
        a.play("waving")          # 换轨迹（非 loop 会停在最后一帧）
        track, idx = a.advance(16)  # 传本次经过的毫秒，返回该画哪一帧
    """

    def __init__(self, track: str = "idle"):
        self.track = track if track in ROW_OF_TRACK else "idle"
        self.index = 0
        self.elapsed = 0.0
        self.loops = 0  # 走完几遍（非 loop 轨迹用来判断"播完了"）

    def play(self, track: str, *, restart: bool = True) -> None:
        track = track if track in ROW_OF_TRACK else "idle"
        if track == self.track and not restart:
            return
        self.track = track
        self.index = 0
        self.elapsed = 0.0
        self.loops = 0

    def _table(self):
        row = ROW_OF_TRACK[self.track]
        return trim_track(TRACKS[self.track], FRAMES[row])

    def advance(self, dt_ms: float) -> tuple[str, int]:
        t = self._table()
        durs = t["durations"]
        self.elapsed += dt_ms
        last = len(durs) - 1
        while self.elapsed >= durs[self.index] and self.index < last:
            self.elapsed -= durs[self.index]
            self.index += 1
        if self.elapsed >= durs[self.index]:
            if t["loop"]:
                self.elapsed -= durs[self.index]
                self.index = 0
                self.loops += 1
            else:
                self.index = last
                self.loops += 1
        return self.track, self.index

    def done(self) -> bool:
        """非 loop 轨迹是否已播完（用于播一下自动回 idle）。"""
        t = self._table()
        return (not t["loop"]) and self.index == len(t["durations"]) - 1

    def delay_ms(self) -> int:
        """当前帧还差多少毫秒结束（给 Tk 的 after 用）。"""
        t = self._table()
        return max(20, int(t["durations"][self.index] - self.elapsed))


__all__ = [
    "Animator",
    "CELL_H",
    "CELL_W",
    "COLUMNS",
    "FRAMES",
    "ROW_OF_TRACK",
    "ROWS",
    "TRACKS",
    "TRACK_NAMES",
    "asset_dir",
    "atlas",
    "cell",
    "load_manifest",
    "trim_track",
]
