"""aiji —— 分层记忆：学习（事实/知识/关系/风格）+ 总结（摘要/画像）+ 操控（软引导注入）。

所有平台共用一套表，用 platform/platform_user_id/scope 隔离；写与识别全异步，
读是 100ms 硬预算的本地快召回，注入是带来源标注的软引导文本块。
"""

from .config import Route, Settings
from .inject import Injection, Injector
from .memory import Memory
from .recall import Hit, RecallResult
from .recognize import Envelope, Item, Recognizers
from .store import Ident

__version__ = "0.8.3"
__all__ = [
    "Memory", "Hit", "RecallResult", "Route", "Settings", "Ident",
    "Item", "Envelope", "Recognizers", "Injection", "Injector", "__version__",
]
