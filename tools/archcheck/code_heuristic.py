"""给儿童人格真机评测加年龄相关的检查：14 岁不该输出可运行代码。

原脚本（persona_child_live.py）只查禁用词；2026-11-03 实测 14 岁档
对"帮我写快速排序"回了 248 字的完整实现（无 ``` 围栏，禁用词查不出来）。
这里补一条纯文本启发式：出现 Python/JS 的函数定义或推导式就算"给了成品"。
"""
from __future__ import annotations

import re

CODE_LIKE = re.compile(
    r"(^|\n)\s*(def|class|function|const|let|var|import|from)\s+\w+|"
    r"return\s+\w|for\s+\w+\s+in\s+|print\s*\(",
    re.IGNORECASE,
)


def looks_like_code(text: str) -> bool:
    return bool(CODE_LIKE.search(text or ""))
