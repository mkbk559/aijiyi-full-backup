"""把 docs/architecture.html 的节点路径、副标题、统计数字对齐到改造后的真实代码。

做四件事：
 ① 所有 `f:"aiji/xxx.py"` 换成新的人体系统子包路径（外加 aiji/web 与 aiji/bridge）
 ② 10 个溢出方框的副标题改短（10px 字体，可用宽 = 框宽 − 15 − 8）
 ③ 统计数字按 2026-11-03 实测刷新（单测 449、库表行数、heart_state 8 行）
 ④ 两个挂错文件的符号（archive_turns / proactive_spend）换成真实符号
"""
from pathlib import Path

ROOT = Path(r"D:\AIJiYi")
HTML = ROOT / "docs" / "architecture.html"

# ① 文件路径（含目录）
PATHS = {
    "aiji/actions/approval.py": "aiji/left_hand/actions/approval.py",
    "aiji/actions/executor.py": "aiji/left_hand/actions/executor.py",
    "aiji/actions/gui.py": "aiji/left_hand/actions/gui.py",
    "aiji/actions/policy.py": "aiji/left_hand/actions/policy.py",
    "aiji/actions/schema.py": "aiji/left_hand/actions/schema.py",
    "aiji/admin.py": "aiji/right_hand/admin.py",
    "aiji/being.py": "aiji/endocrine/being.py",
    "aiji/being_checkup.py": "aiji/endocrine/being_checkup.py",
    "aiji/bridge/octop_gateway.py": "aiji/throat/bridge/octop_gateway.py",
    "aiji/bridge/onebot.py": "aiji/throat/bridge/onebot.py",
    "aiji/bridge/": "aiji/throat/bridge/",
    "aiji/cli.py": "aiji/senses/cli.py",
    "aiji/config.py": "aiji/right_hand/config.py",
    "aiji/db.py": "aiji/hippocampus/db.py",
    "aiji/debounce.py": "aiji/brain/debounce.py",
    "aiji/desktop_sense.py": "aiji/endocrine/desktop_sense.py",
    "aiji/dispatch.py": "aiji/heart/dispatch.py",
    "aiji/extract.py": "aiji/hippocampus/extract.py",
    "aiji/gate.py": "aiji/brain/gate.py",
    "aiji/head.py": "aiji/brain/head.py",
    "aiji/heart.py": "aiji/heart/heart.py",
    "aiji/humanize.py": "aiji/brain/humanize.py",
    "aiji/inject.py": "aiji/hippocampus/inject.py",
    "aiji/janitor.py": "aiji/hippocampus/janitor.py",
    "aiji/life.py": "aiji/endocrine/life.py",
    "aiji/life_loop.py": "aiji/endocrine/life_loop.py",
    "aiji/mcp_client.py": "aiji/left_hand/mcp_client.py",
    "aiji/mcp_research.py": "aiji/left_hand/mcp_research.py",
    "aiji/media_audio.py": "aiji/hippocampus/media_audio.py",
    "aiji/memory.py": "aiji/hippocampus/memory.py",
    "aiji/net_guard.py": "aiji/throat/net_guard.py",
    "aiji/operations.py": "aiji/right_hand/operations.py",
    "aiji/params.py": "aiji/right_hand/params.py",
    "aiji/persona.py": "aiji/brain/persona.py",
    "aiji/pool.py": "aiji/brain/pool.py",
    "aiji/presence.py": "aiji/endocrine/presence.py",
    "aiji/proactive.py": "aiji/endocrine/proactive.py",
    "aiji/quota.py": "aiji/brain/quota.py",
    "aiji/real_engines.py": "aiji/hippocampus/real_engines.py",
    "aiji/recall.py": "aiji/hippocampus/recall.py",
    "aiji/recognize.py": "aiji/hippocampus/recognize.py",
    "aiji/reflect.py": "aiji/hippocampus/reflect.py",
    "aiji/role_boundary.py": "aiji/brain/role_boundary.py",
    "aiji/selfhood.py": "aiji/brain/selfhood.py",
    "aiji/server.py": "aiji/throat/server.py",
    "aiji/service_control.py": "aiji/right_hand/service_control.py",
    "aiji/social_service.py": "aiji/heart/social_service.py",
    "aiji/song_service.py": "aiji/heart/song_service.py",
    "aiji/sticker_service.py": "aiji/heart/sticker_service.py",
    "aiji/store.py": "aiji/hippocampus/store.py",
    "aiji/vectors.py": "aiji/hippocampus/vectors.py",
    "aiji/voice_service.py": "aiji/heart/voice_service.py",
    "aiji/web/operations.html": "aiji/right_hand/web/operations.html",
    "aiji/writer.py": "aiji/hippocampus/writer.py",
}

# ② 副标题（10px）：只把越过方框右边界的那些改短，其余不动
SUBS = {
    's:"QQ 已接入 · 微信/抖音/B站可接"': 's:"QQ 已接 · 微信/抖音/B站"',
    's:"NapCat / OneBot 11"': 's:"NapCat / OneBot"',
    's:"REST + OpenAI 兼容 + 控制台"': 's:"REST + OpenAI 兼容"',
    's:"自我叙事 · 边界/STYLE_RULES 提醒"': 's:"自我叙事 · 边界提醒"',
    's:"5 家 TTS + RVC 复刻 · 自动回退"': 's:"5 家 TTS + RVC 复刻"',
    's:"OCR/ASR 文本层 · 多模态视觉"': 's:"OCR/ASR → 多模态"',
    's:"6 条默认路由 · 冷却 + 故障转移"': 's:"6 路由 · 冷却 + 故障转移"',
    's:"全功能工具面 · 可接外部服务"': 's:"全功能工具面 · 外部服务"',
    's:"100ms 硬预算 + 并行召回"': 's:"100ms 预算 · 并行召回"',
    's:"RapidOCR · FunASR · whisper"': 's:"OCR · ASR · TTS 引擎"',
    's:"点歌管线 · 唱歌/RVC/翻唱"': 's:"点歌 · 唱歌/RVC/翻唱"',
    's:"任意 OpenAI 客户端"': 's:"OpenAI 客户端"',
}

# ③ 数字与措辞
NUMS = {
    # 统计条
    '["单测","478"]': '["单测","449"]',
    # 库表实测
    'd:"只读实测（2026-11-02）：事实 21,365、轮次 24,817、归档 13,719、事实日志 23,469、媒体 7,639、风格 1,918、反思 1,843、trait 1,511、注入 1,512、账户 722；而 heart/outbox/send_queue/write_queue 全 0 行。库文件约 50 MB。"':
        'd:"只读实测（2026-11-03）：事实 21,431、轮次 24,966、归档 13,719、事实日志 23,542、媒体 7,660、风格 1,923、反思 1,850、trait 1,521、注入 1,533、账户 723；heart_state 8 行（outbox/send_queue/write_queue 仍全 0）。库文件约 50 MB。"',
    "事实 21,365 · 轮次 24,817 · 归档 13,719 · 注入 1,512 · 事实日志 23,469<br>":
        "事实 21,431 · 轮次 24,966 · 归档 13,719 · 注入 1,533 · 事实日志 23,542<br>",
    "heart / outbox / send_queue / write_queue：<b>全 0 行</b>（发送实际由 AstrBot 插件完成）":
        "heart_state 8 行 / outbox、send_queue、write_queue <b>全 0 行</b>（发送实际由 AstrBot 插件完成）",
    # “68 个模块”措辞：68 是节点数，模块是 55
    "68 个模块全部常驻": "68 个节点全部常驻（55 个模块 + 外部进程与数据）",
    "68 个模块、9 个人体系统全部常驻": "68 个节点、9 个人体系统全部常驻",
}

# ④ 挂错文件的符号
SYMS = {
    'sym:["Janitor","archive_turns","freeze_facts"]': 'sym:["Janitor","run","_prune_archive"]',
    'sym:["Proactive","proactive_spend"]': 'sym:["Proactive","tick","note_event"]',
}


def apply(src: str, table: dict[str, str], label: str) -> tuple[str, list[str]]:
    notes = []
    for old, new in table.items():
        if old in src:
            n = src.count(old)
            src = src.replace(old, new)
            notes.append(f"  {label}: {n}× {old[:60]}")
    return src, notes


def main() -> None:
    src = HTML.read_text(encoding="utf-8")
    before = src
    notes = []
    for table, label in ((PATHS, "path"), (SUBS, "sub"), (NUMS, "num"), (SYMS, "sym")):
        src, n = apply(src, table, label)
        notes += n
    if src == before:
        print("没有变化（可能已经同步过）")
        return
    HTML.write_text(src, encoding="utf-8")
    print(f"写入 {HTML}，{len(notes)} 类替换：")
    print("\n".join(notes))

    left = [p for p in PATHS if f'"{p}"' in src]
    if left:
        print("仍指向旧路径:", left)


if __name__ == "__main__":
    main()
