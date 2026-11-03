"""把索引文件名里剩下的 dafeiyu 也改掉，并让 profile.json 的路径与其真实文件一致。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RVC = ROOT / "voice-engine" / "rvc" / "assets"
OLD, NEW = "dafeiyu", "makabaka"


def main() -> None:
    for path in list(RVC.glob("indices/*.index")) + list(RVC.glob("logs/**/*")):
        if path.is_file() and OLD in path.name:
            target = path.with_name(path.name.replace(OLD, NEW))
            path.rename(target)
            print("file →", target.name)

    profile_path = ROOT / "data" / "voices" / NEW / "profile.json"
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    post = data.get("postprocess", {})
    for key in ("model", "index"):
        raw = post.get(key)
        if not isinstance(raw, str):
            continue
        candidate = ROOT / raw
        if not candidate.exists():
            for path in RVC.rglob(Path(raw).name.replace(OLD, NEW)):
                post[key] = str(path.relative_to(ROOT)).replace("\\", "/")
                break
        print(key, "→", post[key], "exists:", (ROOT / post[key]).exists())
    profile_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main()
