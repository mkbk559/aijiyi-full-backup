"""跟着身份重塑改测试：断言从"大肥鱼/电脑里"改成"玛卡巴卡/人类小孩"。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAIRS = {
    "tests/test_selfhood.py": [
        ('        self.assertIn("大肥鱼", text)',
         '        self.assertIn("玛卡巴卡", text)\n'
         '        # 旧设定（鱼 / 住在电脑里）不得再出现在提示词里\n'
         '        for gone in ("大肥鱼", "一条鱼", "鱼缸", "住在电脑"):\n'
         '            self.assertNotIn(gone, text)'),
        ('        self.assertIn("大肥鱼", p)',
         '        self.assertIn("玛卡巴卡", p)'),
        ('        self.assertNotIn("你是一条大肥鱼", p)\n'
         '        self.assertIn("像普通群友", p)\n'
         '        self.assertNotIn("你有人的情绪", p)\n'
         '        self.assertIn("情绪反应可以像真实的人", p)',
         '        self.assertNotIn("你是一条大肥鱼", p)\n'
         '        self.assertIn("像熟人一样直接接话", p)\n'
         '        self.assertIn("情绪直接", p)\n'
         '        self.assertNotIn("不要声称自己是人类", p)'),
    ],
    "tests/test_console_api.py": [
        ('        self.assertIn("大肥鱼", portrait["floor"]["物种"])',
         '        self.assertIn("玛卡巴卡", portrait["floor"]["身份"])'),
    ],
    "tests/test_openai_api.py": [
        ('        self.assertIn("大肥鱼", prompt)',
         '        self.assertIn("玛卡巴卡", prompt)'),
        ('            "（甩尾巴）我是大肥鱼，不是模型。",',
         '            "（吐舌头）你才是模型，我是玛卡巴卡。",'),
        ('            "（甩尾巴）我是鱼，不会写代码。",',
         '            "（吐舌头）我写不了代码，你找大人写嘛。",'),
    ],
    "tests/test_style_rewrite.py": [
        ('        self.assertIn("我是大肥鱼", reply.text)',
         '        self.assertIn("我是玛卡巴卡", reply.text)'),
        ('            "我是个虚拟存在，一条大肥鱼，不是真人也不是人机。",',
         '            "我是个虚拟存在，不是真人也不是人机。",'),
    ],
    "tests/test_being_checkup.py": [
        ('            "（甩尾巴）你才 AI，我是大肥鱼。",',
         '            "（吐舌头）你才是 AI，我是玛卡巴卡。",'),
    ],
    "tests/test_persona_child.py": [
        ('        self.assertIn("这个我不懂，我要问妈妈", block)\n'
         '        self.assertIn("不装懂", block)\n'
         '        self.assertIn("不假装有真实身体", block)\n'
         '        self.assertIn("我在电脑里呀", block)',
         '        self.assertIn("这个我不懂，我要问妈妈", block)\n'
         '        self.assertIn("不装懂", block)\n'
         '        self.assertIn("不承认自己是被造出来的程序", block)\n'
         '        # 新设定里他就是人：不许再出现"在电脑里/屏幕/鱼"这类旧身份词\n'
         '        for gone in ("在电脑里", "鱼", "屏幕之外"):\n'
         '            self.assertNotIn(gone, block)'),
        ('        # 底线说"你住在电脑里、看不见屏幕外"，儿童设定不能声称有真实身体\n'
         '        self.assertNotIn("小孩", selfhood.floor_text())',
         '        # 底线已经改成"六七岁的人类小孩玛卡巴卡"，儿童设定和它必须一致\n'
         '        self.assertIn("六七岁", selfhood.floor_text())\n'
         '        self.assertNotIn("住在电脑", selfhood.floor_text())'),
    ],
}

missing: list[str] = []
for rel, pairs in PAIRS.items():
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in text:
            missing.append(f"{rel}: {old[:70]!r}")
            continue
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")

if missing:
    print("MISS:")
    for item in missing:
        print(" ", item)
    sys.exit(1)
print("patched", len(PAIRS), "files")
