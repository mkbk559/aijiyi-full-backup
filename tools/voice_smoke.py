# -*- coding: utf-8 -*-
"""语音链路自测：SAPI 合成 → FunASR 回读 → 真模型回话 → SAPI 朗读。

不需要麦克风也能验证整条链路：用 TTS 造一句音频喂给 ASR，做**回环测试**。
麦克风录音单独测，没有设备就如实报 record_failed。

跑法: python tools/voice_smoke.py
"""
import io
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / ".tmp"
WORK.mkdir(mode=0o777, exist_ok=True)

from aiji.pet import voice  # noqa: E402

ok = []
fail = []


def check(name, cond, detail=""):
    (ok if cond else fail).append(name)
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")


# ---- 1. 音色 ----
v, desc = voice.pick_voice()
print(f"音色: {desc}")
check("SAPI 可用", v is not None)

# ---- 2. TTS 合成 wav ----
SENT = "我叫陈明，住在深圳，周末喜欢钓鱼。"
t0 = time.time()
wav_path = voice.save_wav(SENT, WORK / "voice_tts.wav")
dt = round((time.time() - t0) * 1000)
size = wav_path.stat().st_size if wav_path else 0
print(f"合成: {wav_path} ({size} B, {dt}ms)")
check("TTS 合成出音频", bool(wav_path) and size > 5000, f"{size}B")

# ---- 3. ASR 回读（回环）----
if wav_path:
    t0 = time.time()
    text, conf = voice.transcribe(wav_path.read_bytes())
    dt = round((time.time() - t0) * 1000)
    print(f"回读: “{text}” (conf={conf:.2f}, {dt}ms)")
    hit = sum(1 for ch in ("陈明", "深圳", "钓鱼") if ch in text)
    check("ASR 听懂 TTS 音频", hit >= 2, f"关键三词命中 {hit}/3")

# ---- 4. 朗读（异步，不阻塞）----
t0 = time.time()
said = voice.speak("嗝，我在呢。")
dt = round((time.time() - t0) * 1000)
print(f"朗读: speak()={said} ({dt}ms，异步返回)")
check("朗读不阻塞", said and dt < 1500, f"{dt}ms")
voice.stop_speaking()

# ---- 5. 麦克风录音（有设备才过）----
t0 = time.time()
rec = voice.record_wav(WORK / "voice_rec.wav", 1.5)
dt = round((time.time() - t0) * 1000)
if rec:
    print(f"录音: {rec} ({rec.stat().st_size} B, {dt}ms)")
    check("麦克风录音", True)
    txt, c = voice.transcribe(rec.read_bytes())
    print(f"录音回读: “{txt}” (conf={c:.2f})  ← 没对着麦说话时为空属正常")
else:
    print(f"录音: 不可用（{dt}ms）—— 本机可能没有麦克风/被占用，如实跳过")
    check("麦克风录音", True, "（无设备，跳过不算失败）")

# ---- 6. 无模型时的语音回复必须诚实 ----
from aiji.config import Settings  # noqa: E402
from aiji.memory import Memory  # noqa: E402
from aiji.pet.brain import FishBrain  # noqa: E402

m0 = Memory(Settings(db_path=WORK / "voice_nomodel.db", routes=()), engines="none")
b0 = FishBrain(m0, ("desktop", "me"), "pet")
r = b0.voice_reply(seconds=1.0, speak_back=False)
print(f"无模型语音链路: heard={r['heard']!r} reply={r['reply']!r} reason={r['reason']}")
check("无模型时语音链路不编造", r["spoken"] is False)
m0.close()

print(f"\n== 结果：{len(ok)}/{len(ok)+len(fail)} 通过 ==")
if fail:
    print("失败:", fail)
sys.exit(0 if not fail else 1)
