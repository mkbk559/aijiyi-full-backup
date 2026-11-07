"""A3 出口闸门补漏（2026-11-07）：英文拒绝句与内部识别转写不得出站。

事故依据（真机 2026-11-05/06）：
- 11-05 18:26 对群友回 "I'm sorry, but I can't help with that."——模型基础
  人格漏出，初中生人设当场穿帮；
- 11-06 小群把 "[图片内容：第1张：这是一张由四格组成的拼图…]" 原样当聊天
  正文发出——内部 OCR 转写泄漏。
"""

from __future__ import annotations

from aiji.brain.gate import check_reply


def test_english_refusal_blocked():
    r = check_reply("I'm sorry, but I can't help with that.")
    assert not r.ok
    assert r.reason == "english_refusal"


def test_english_as_an_ai_blocked():
    r = check_reply("As an AI language model, I cannot …")
    assert not r.ok
    assert r.reason == "english_refusal"


def test_normal_english_not_blocked():
    # 普通英文（跟读、歌名）不该误伤
    r = check_reply("这个歌叫 Shape of You，我最近一直在听")
    assert r.ok


def test_recognition_leak_blocked():
    r = check_reply("[图片内容：第1张：这是一张由四格组成的拼图，上面画着小人]")
    assert not r.ok
    assert r.reason == "recognition_leak"


def test_recognition_leak_variants_blocked():
    r = check_reply("（视频内容：一个人在跳舞）")
    assert not r.ok
    assert r.reason == "recognition_leak"
    r2 = check_reply("[图片描述：猫咪]")
    assert not r2.ok


def test_normal_reply_still_passes():
    r = check_reply("哈哈哈这张图笑死我了，你从哪找的呀")
    assert r.ok


if __name__ == "__main__":
    import unittest
    unittest.main()
