"""受控电脑操作层：文件、终端与桌面工具，默认人工审批。"""

from .approval import ApprovalManager
from .executor import ActionExecutor, ToolResult
from .policy import ActionPolicy, PolicyViolation
from .schema import OPENAI_TOOLS, ToolCall

__all__ = [
    "ActionExecutor", "ActionPolicy", "ApprovalManager", "OPENAI_TOOLS",
    "PolicyViolation", "ToolCall", "ToolResult",
]
