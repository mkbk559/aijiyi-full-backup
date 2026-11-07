"""把一句话变成玛卡巴卡的语音条：文本 → edge-tts → (可选) RVC 变声 → wav。

用法：
    python tools/make_voice.py "晚安啦，明天见" -o data/temp/voice.wav

为什么单独一个工具：语音链路要能**脱离聊天**单独验（听得见、时长对），
出问题才好分清是"生成坏了"还是"发送坏了"。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

RVC = "http://127.0.0.1:18601"


def tts(text: str, out: Path, voice: str = "zh-CN-XiaoxiaoNeural") -> Path:
    """edge-tts 合成 mp3。需要出网；不通就抛错，让调用方决定降级。"""
    import edge_tts

    async def _run() -> None:
        await edge_tts.Communicate(text, voice).save(str(out))

    asyncio.run(_run())
    return out


def rvc(src: Path, out: Path) -> Path:
    """可选：送本地 18601（tools/rvc_server.py）变成玛卡巴卡声线；引擎没起就原样返回。

    服务端接口是 POST /convert {"in": 源文件, "out": 目标文件}，
    不是 /infer（2026-11-06 实测 404 踩过）。
    """
    body = json.dumps({"in": str(src.resolve()), "out": str(out.resolve())}).encode("utf-8")
    req = urllib.request.Request(f"{RVC}/convert", data=body,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] RVC 不可用（{exc}），用原始 TTS 声音", file=sys.stderr)
        return src
    produced = Path(payload.get("out") or out)
    return produced if produced.exists() else src


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成一条语音")
    ap.add_argument("text")
    ap.add_argument("-o", "--out", default="data/temp/voice.mp3")
    ap.add_argument("--no-rvc", action="store_true", help="只用 TTS 原声，不变声")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    mp3 = out.with_suffix(".mp3")
    tts(args.text, mp3)
    final = mp3 if args.no_rvc else rvc(mp3, out)
    print(json.dumps({"ok": True, "path": str(final),
                      "bytes": final.stat().st_size,
                      "seconds_hint": round(len(args.text) / 5.5, 1)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
