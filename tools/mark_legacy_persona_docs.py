"""给"还写着大肥鱼/鱼缸"的历史文档打横幅。

2026-11-03 人格定稿为「玛卡巴卡」（约 6 岁半的小孩），鱼设相关的一切已清空。
历史文档不改内容（是当时的证据），只在开头说明"这套设定已废弃"，
免得新会话读到 design/dafeiyu-plan.md 以为现在还是那条鱼。

跳过：本身就是"这次改了人格"的证据/交接文件（它们本来就在讲这件事）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(r"D:\AIJiYi\docs\agent")
BANNER = (
    "> **历史文档（设定已废弃）**：这里的「大肥鱼 / 鱼缸」是 2026-11-03 之前的人设，\n"
    "> 那一天已整体替换为「玛卡巴卡」（约 6 岁半的小孩，见\n"
    "> [persona-child-2026-11-03.md](persona-child-2026-11-03.md)），相关记忆与素材均已清空。\n"
    "> 本文只作当时的决策/研究记录，不要当作现行设定。\n\n"
)
SKIP = {
    "PROJECT_STATE.md",
    "excluded.md",
    "status.md",
    "persona-child-2026-11-03.md",
    "persona-purge-2026-11-03.md",
    "audit-2026-11-03-round2.md",
}


def main() -> int:
    touched, skipped = [], []
    for path in sorted(ROOT.rglob("*.md")):
        if path.name in SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        if "大肥鱼" not in text and "鱼缸" not in text:
            continue
        if "设定已废弃" in text:
            skipped.append(path)
            continue
        # 相对链接在子目录里可能失效：统一用绝对仓库路径感的写法
        banner = BANNER.replace(
            "[persona-child-2026-11-03.md](persona-child-2026-11-03.md)",
            "`docs/agent/design/persona-child-2026-11-03.md`")
        path.write_text(banner + text, encoding="utf-8")
        touched.append(path)
    print(f"加了横幅 {len(touched)} 份，已有横幅 {len(skipped)} 份")
    for p in touched:
        print("  ", p.relative_to(ROOT.parent.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
