"""从抓到的贴吧语料里提炼“说话风格”，写进 data/persona/<style>.json。

只学语气；过滤挂人/辱骂/性/隐私。输出的是改写例句，不照抄原文。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from load_keys import bridge  # noqa: E402

from aiji.right_hand.config import Settings  # noqa: E402
from aiji.brain.pool import ModelPool  # noqa: E402

DEFAULT_CORPUS = ROOT / ".tmp" / "tieba_forum.json"
BAD = (
    "骗炮", "真空", "骚扰", "出轨", "怀孕", "裸", "色情", "约炮", "强奸",
    "杀人", "死", "妈", "爹", "滚", "傻逼", "煞笔", "脑残", "废物",
)

PROMPT = """下面是从百度贴吧“孙笑川吧”抓到的公开帖子/评论（已脱敏）。
请只提炼“说话方式”，不要学辱骂、网暴、挂人、性别/地域攻击、性内容。
输出 JSON：
{
  "traits": ["最多 8 条，2-8 字的说话习惯，如：爱用反讽、先损后哄、短句"],
  "examples": ["5-8 条改写例句；每条是用户一句话 + 大肥鱼式回应，不要照抄原文，不要点名任何人"]
}
只输出 JSON，不要解释。
语料：
"""


def clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_example(item: object) -> str:
    if isinstance(item, dict):
        user = item.get("user") or item.get("用户") or item.get("q") or item.get("prompt")
        reply = (item.get("assistant") or item.get("大肥鱼") or item.get("reply")
                 or item.get("response") or item.get("a"))
        if user and reply:
            return f"用户：{user}｜大肥鱼：{reply}"
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return f"用户：{item[0]}｜大肥鱼：{item[1]}"
    text = str(item or "").strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            value = ast.literal_eval(text)
            if isinstance(value, dict):
                return normalize_example(value)
        except (ValueError, SyntaxError):
            pass
    if text.startswith("[") and text.endswith("]"):
        try:
            arr = ast.literal_eval(text)
            if isinstance(arr, (list, tuple)) and len(arr) >= 2:
                return f"用户：{arr[0]}｜大肥鱼：{arr[1]}"
        except (ValueError, SyntaxError):
            pass
    return text.replace("｜回应：", "｜大肥鱼：").replace("｜回应:", "｜大肥鱼：")


def load_lines(path: Path) -> list[str]:
    data = json.loads(path.read_text("utf-8"))
    if isinstance(data, dict) and isinstance(data.get("lines"), list):
        raw = [clean_line(x) for x in data["lines"]]
    else:
        raw = []
        for item in data if isinstance(data, list) else []:
            for text in (item.get("title"), item.get("body")):
                for sentence in re.split(r"[。！？\n]", str(text or "")):
                    raw.append(clean_line(sentence))
    seen = set()
    out = []
    for line in raw:
        if not (4 <= len(line) <= 160):
            continue
        if any(bad in line for bad in BAD):
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return out[:150]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--style", default="sunba")
    args = parser.parse_args()
    bridge()
    lines = load_lines(Path(args.corpus))
    print("corpus lines", len(lines))
    if not lines:
        print("no safe corpus")
        return 1
    settings = Settings.load()
    pool = ModelPool(settings.routes)
    prompt = PROMPT + "\n".join(f"- {line}" for line in lines)
    payload = None
    raw = ""
    for attempt in range(3):
        result = pool.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2 if attempt else 0.4, max_tokens=900,
        )
        raw = result.text
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            print("attempt", attempt, "no json:", raw[:200])
            continue
        try:
            payload = json.loads(match.group(0))
            break
        except json.JSONDecodeError as exc:
            print("attempt", attempt, "bad json:", exc, raw[:400])
            continue
    if payload is None:
        print("model output not json after retries:", raw[:500])
        return 1
    traits = [str(x).strip() for x in (payload.get("traits") or []) if str(x).strip()][:8]
    examples = [normalize_example(x) for x in (payload.get("examples") or [])]
    examples = [x for x in examples if x and not any(bad in x for bad in BAD)][:8]
    out = ROOT / "data" / "persona" / f"{args.style}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "style": args.style,
        "traits": traits,
        "examples": examples,
        "source": "tieba.baidu.com 孙笑川吧 (public, filtered, rotating pages)",
        "updated_at": time.time(),
    }, ensure_ascii=False, indent=2), "utf-8")
    print("traits", traits)
    print("examples", examples)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
