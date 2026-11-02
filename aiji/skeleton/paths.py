"""aiji.skeleton.paths —— ⑨ 骨架：仓库根与数据地基的唯一来源。

任何模块要读 data/、config/、tools/、bench/、web/ 都从这里取，
不要再写 Path(__file__).resolve().parents[N]——身体再分一层这种写法必炸。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # aiji/skeleton/paths.py → 仓库根
AIIJI = ROOT / "aiji"
DATA = ROOT / "data"
CONFIG = ROOT / "config"
TOOLS = ROOT / "tools"
BENCH = ROOT / "bench"
WEB_DIR = AIIJI / "right_hand" / "web"

__all__ = ["ROOT", "AIIJI", "DATA", "CONFIG", "TOOLS", "BENCH", "WEB_DIR"]
