"""Physical-action boundary for IM role chat."""

from __future__ import annotations

import re


_ACTION = re.compile(
    r"(写|生成|来|给).{0,12}(代码|脚本|程序|爬虫|函数|python|javascript|java|"
    r"c\+\+|sql|正则)|"
    r"(运行|执行|操作|控制).{0,8}(电脑|终端|命令|程序|文件)|"
    r"(帮我|替我).{0,6}(点|打开|关闭|安装|下载|删除|转账|支付)",
    re.IGNORECASE,
)
_CODE_LIKE = re.compile(
    r"```|(^|\n)\s*(?:def|class|import|from|function|const|let|var|"
    r"SELECT|INSERT|UPDATE|DELETE)\s+|print\s*\(",
    re.IGNORECASE,
)
_IDENTITY_LEAK = re.compile(
    r"(虚拟存在|虚拟角色|大语言模型|语言模型|作为.{0,6}(?:AI|人工智能)|"
    r"不是真人|不是人机|AI助手)",
    re.IGNORECASE,
)


def is_physical_action_request(text: str) -> bool:
    return bool(_ACTION.search(str(text or "")))


def contains_executable_code(text: str) -> bool:
    return bool(_CODE_LIKE.search(str(text or "")))


def contains_identity_disclosure(text: str) -> bool:
    """Detect assistant-style identity disclosures that break IM roleplay."""
    return bool(_IDENTITY_LEAK.search(str(text or "")))


def identity_repair(text: str) -> str:
    variants = (
        "我就是大肥鱼，群里聊过几次你就知道了。",
        "查户口呢？先聊正事。",
        "我是大肥鱼，没换号。",
    )
    return variants[len(str(text or "")) % len(variants)]


def prompt_block() -> str:
    return (
        "## 物理动作边界（优先于聊天风格）\n"
        "不要假装自己能直接操作用户的设备或账户。"
        "当对方要求写可运行代码、执行命令、控制电脑、打开/删除文件、转账或支付时，"
        "用一两句普通群聊的话说明自己做不到，并让对方找能动手的人；"
        "不要输出代码块、函数、命令或假装已经执行。"
    )


def refusal(text: str) -> str:
    variants = (
        "这事我没法替你直接操作设备，得你自己动手。",
        "代码我这边不写，你找能实际处理的人吧。",
        "这个我操作不了，得你自己来。",
    )
    return variants[len(str(text or "")) % len(variants)]


__all__ = [
    "contains_executable_code",
    "contains_identity_disclosure",
    "identity_repair",
    "is_physical_action_request",
    "prompt_block",
    "refusal",
]
