"""把声线包 `dafeiyu`（大肥鱼）改名成 `makabaka`（玛卡巴卡），对齐新身份。

只改**路径与显示名**，不动音频内容：
- data/voices/dafeiyu → data/voices/makabaka
- 权重/索引文件名 dafeiyu* → makabaka*
- profile.json: id / name / postprocess.model / postprocess.index
- data/voices/active_voice.txt
- tools/archcheck/results/nodes.json 里给控制台看的说明文字

权重是同盘 rename（瞬时，不复制 440MB）。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICES = ROOT / "data" / "voices"
OLD, NEW = "dafeiyu", "makabaka"
DISPLAY_OLD, DISPLAY_NEW = "大肥鱼", "玛卡巴卡"


def rename_tree(src: Path) -> None:
    if src.exists():
        target = src.parent / NEW
        if target.exists():
            raise SystemExit(f"目标已存在，先人工确认：{target}")
        src.rename(target)
        print("dir →", target.relative_to(ROOT))


def main() -> None:
    rename_tree(VOICES / OLD)
    profile_dir = VOICES / NEW

    # 权重 / 索引文件名
    for folder in ("weights", "indices", "logs"):
        for path in (ROOT / "voice-engine" / "rvc" / "assets" / folder).glob(f"{OLD}*"):
            path.rename(path.with_name(path.name.replace(OLD, NEW, 1)))
            print("file →", path.name.replace(OLD, NEW, 1))

    # profile.json 内部引用
    profile_path = profile_dir / "profile.json"
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    data["id"] = NEW
    data["name"] = DISPLAY_NEW
    post = data.get("postprocess", {})
    for key in ("model", "index"):
        if isinstance(post.get(key), str):
            post[key] = post[key].replace(OLD, NEW)
    profile_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print("profile id/name →", data["id"], data["name"])

    # 激活声线
    active = VOICES / "active_voice.txt"
    if active.exists() and active.read_text(encoding="utf-8").strip() == OLD:
        active.write_text(NEW + "\n", encoding="utf-8")
        print("active_voice →", NEW)

    # 控制台说明文字
    nodes = ROOT / "tools" / "archcheck" / "results" / "nodes.json"
    text = nodes.read_text(encoding="utf-8")
    before = text
    text = text.replace(f"{OLD}.pth", f"{NEW}.pth").replace(DISPLAY_OLD, DISPLAY_NEW)
    if text != before:
        nodes.write_text(text, encoding="utf-8")
        print("nodes.json 已更新")

    # 架构文档
    arch = ROOT / "docs" / "architecture.html"
    text = arch.read_text(encoding="utf-8")
    before = text
    text = text.replace(f"换成{DISPLAY_OLD}角色声线", f"换成{DISPLAY_NEW}角色声线")
    if text != before:
        arch.write_text(text, encoding="utf-8")
        print("architecture.html 已更新")


if __name__ == "__main__":
    main()
