"""按"引擎清单"从 HuggingFace 拉模型文件（断点续传 + 落盘校验 + 写 lock）。

设计目标（这是"升级便利化"的一半）：换/升引擎时不用手工往 checkpoints/ 塞文件，
一条命令拿到**确定的**文件集合，并把 sha256 记进 engine.lock.json，升级可回滚。

用法：
    python tools/engine_fetch_models.py --list                       # 只看清单与体积
    python tools/engine_fetch_models.py --fetch                      # 下载缺失文件
    python tools/engine_fetch_models.py --fetch --engine cosyvoice2  # 换引擎

镜像策略：默认 huggingface.co 直连（本机实测 ~0.96 MB/s），失败退 hf-mirror.com。
不碰 modelscope：本机对 www.modelscope.cn 有 SSL UNEXPECTED_EOF（2026-11-02 实测）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIRRORS = ["https://huggingface.co", "https://hf-mirror.com"]

# 每个引擎需要的文件（相对 <engine_dir> 的落地路径）。只列**推理**必需项，训练/ASR 不列。
ENGINES: dict[str, dict] = {
    "gpt-sovits": {
        "repo": "lj1995/GPT-SoVITS",
        "dest": "voice-engine/gpt-sovits/GPT_SoVITS/pretrained_models",
        "files": [
            # v2 主权重（README "Use v2 from v1 environment" 段）
            "gsv-v2final-pretrained/s2G2333k.pth",
            "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
            # 中文文本/语音特征前端
            "chinese-hubert-base/config.json",
            "chinese-hubert-base/preprocessor_config.json",
            "chinese-hubert-base/pytorch_model.bin",
            "chinese-roberta-wwm-ext-large/config.json",
            "chinese-roberta-wwm-ext-large/tokenizer.json",
            "chinese-roberta-wwm-ext-large/pytorch_model.bin",
        ],
    },
    "cosyvoice2": {
        "repo": "FunAudioLLM/CosyVoice2-0.5B",
        "dest": "voice-engine/cosyvoice2/pretrained_models/CosyVoice2-0.5B",
        "files": [],  # 用 --discover 自动展开
    },
}

LOCK = ROOT / "voice-engine" / "engine.lock.json"


def tree(repo: str, timeout: int = 30) -> list[dict]:
    last: Exception | None = None
    for base in MIRRORS:
        try:
            url = f"{base}/api/models/{repo}/tree/main?recursive=true"
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.load(r)
            return [d for d in data if d.get("type") == "file"]
        except Exception as exc:  # noqa: BLE001 - 换镜像重试
            last = exc
    raise SystemExit(f"列目录失败（{repo}）：{last}")


def discover(engine: str) -> list[str]:
    repo = ENGINES[engine]["repo"]
    return [d["path"] for d in tree(repo)]


def human(n: float) -> str:
    return f"{n / 1048576:.1f} MB"


def fetch_one(repo: str, rel: str, dest: Path, timeout: int = 60) -> str:
    """下载单个文件，支持断点续传；返回 sha256。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for base in MIRRORS:
        url = f"{base}/{repo}/resolve/main/{rel}"
        try:
            have = part.stat().st_size if part.exists() else 0
            req = urllib.request.Request(url, headers={"User-Agent": "aiji-engine-fetch"})
            if have:
                req.add_header("Range", f"bytes={have}-")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                total = int(r.headers.get("Content-Length") or 0) + have
                mode = "ab" if have and r.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                done = have
                t0 = time.time()
                with part.open(mode) as fh:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if done % (16 << 20) < (1 << 20):
                            rate = done / max(time.time() - t0, 0.01) / 1048576
                            sys.stdout.write(
                                f"\r    {rel[-46:]:46} {human(done)}"
                                f"/{human(total)} {rate:.2f} MB/s   ")
                            sys.stdout.flush()
            print()
            if dest.exists():
                dest.unlink()
            part.replace(dest)
            h = hashlib.sha256()
            with dest.open("rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            return h.hexdigest()
        except Exception as exc:  # noqa: BLE001 - 换镜像重试
            print(f"\n    镜像失败 {base}: {type(exc).__name__} {str(exc)[:70]}")
            continue
    raise SystemExit(f"下载失败：{rel}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="gpt-sovits", choices=sorted(ENGINES))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--discover", action="store_true", help="从上游仓库展开完整文件清单")
    ap.add_argument("--fetch", action="store_true")
    args = ap.parse_args(argv)

    spec = ENGINES[args.engine]
    repo = spec["repo"]
    dest_root = ROOT / spec["dest"]
    files = discover(args.engine) if args.discover else list(spec["files"])
    if not files:
        raise SystemExit("清单为空：加 --discover 先展开，或把需要的文件写进 ENGINES")

    sizes = {d["path"]: d["size"] for d in tree(repo)}
    total = sum(sizes.get(f, 0) for f in files)
    print(f"引擎 {args.engine} ← {repo}，{len(files)} 个文件，共 {human(total)}")
    for f in files:
        have = (dest_root / f).exists()
        print(f"  {'✓' if have else ' '} {human(sizes.get(f, 0)):>10}  {f}")

    if args.list or not args.fetch:
        return 0

    lock_path = LOCK
    lock = json.loads(lock_path.read_text("utf-8")) if lock_path.exists() else {}
    entry = lock.setdefault(args.engine, {"repo": repo, "commit": None, "files": {}})
    t0 = time.time()
    for f in files:
        target = dest_root / f
        want = sizes.get(f, 0)
        if target.exists() and target.stat().st_size == want:
            print(f"  跳过（已完整）{f}")
        else:
            print(f"  下载 {human(want)}  {f}")
            entry["files"][f] = fetch_one(repo, f, target)
    entry["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n完成，用时 {(time.time() - t0) / 60:.1f} 分钟；lock 写入 {lock_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
