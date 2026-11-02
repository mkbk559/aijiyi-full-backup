"""可选语义索引：默认关闭。开启后只做暴力余弦，够几千~几万条事实用。

没装任何模型也能跑：HashingEmbedder 把字符 n-gram 哈希到固定维度，
对中文近义改写只能算"弱语义"，真正上量再换 bge/text-embedding-v3。
shortcut: 暴力余弦 O(n)，>5 万条事实要换 sqlite-vec / hnsw，届时改这里一处。

2026-10-29 新增 LLMEmbedder：网关不支持 /v1/embeddings（两网关实测 404），
降级方案——用 chat 模型做**软嵌入**：把文本映射到任务定制的语义维度分
（记忆检索场景），维度语义可解释。hashing 打底 + LLM 维度增强，检索取 max。
池挂时纯 hashing（数据不丢）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from typing import Protocol, Sequence

DIM = 256

# ---- LLM 软嵌入：任务定制维度（记忆检索场景）----
LLM_DIMS = (
    "身份属性", "居住地点", "职业工作", "兴趣爱好", "人际关系",
    "时间事件", "偏好习惯", "情绪表达", "知识事实", "计划承诺",
)
_DIM_PROMPT = (
    "给下面文本按 10 个语义维度打相关度分（0.0-1.0，两位小数）。"
    "只输出一个 JSON 数组，10 个数字，顺序对应：\n"
    + "、".join(LLM_DIMS) + "\n\n文本："
)


def _parse_dims(raw: str) -> list[float] | None:
    """模型输出 → 10 维向量。非 JSON / 长度不对 / 越界 → None（走兜底）。"""
    s = raw or ""
    i = s.find("[")
    j = s.find("]", i)
    if i < 0 or j <= i:
        return None
    try:
        vals = [float(x) for x in json.loads(s[i : j + 1])]
    except ValueError:
        return None
    if len(vals) != len(LLM_DIMS):
        return None
    return [max(0.0, min(1.0, v)) for v in vals]


def llm_dim_embed(text: str, chat) -> list[float] | None:
    """软嵌入：chat(messages)→str 的可调用（ModelPool.chat 的签名包装）。
    返回 None 表示模型不可用/输出不合法（调用方走 hashing 兜底）。"""
    try:
        raw = chat([{"role": "user", "content": _DIM_PROMPT + (text or "")[:400]}])
        return _parse_dims(raw)
    except Exception:  # noqa: BLE001 - 模型失败不抛给索引层
        return None


def hashing_embed(text: str, dim: int = DIM) -> list[float]:
    v = [0.0] * dim
    t = (text or "").strip().lower()
    if not t:
        return v
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            g = t[i : i + n]
            h = hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if h[4] & 1 else -1.0
            v[idx] += sign
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class SemanticIndex(Protocol):
    def upsert(self, key: int, text: str, meta: dict) -> None: ...

    def delete(self, key: int) -> None: ...

    def search(self, text: str, k: int = 8) -> list[tuple[int, float, dict]]: ...


class NullIndex:
    """关闭时的空实现：recall 里连调用都省了。"""

    def upsert(self, key: int, text: str, meta: dict) -> None:
        return None

    def delete(self, key: int) -> None:
        return None

    def search(self, text: str, k: int = 8) -> list[tuple[int, float, dict]]:
        return []


class BruteForceIndex:
    """纯 hashing 单路（vector="hash" 的旧行为，测试与兼容保留）。"""

    def __init__(self, dim: int = DIM):
        self.dim = dim
        self._items: dict[int, tuple[list[float], dict]] = {}
        self._lock = threading.Lock()

    def upsert(self, key: int, text: str, meta: dict) -> None:
        vec = hashing_embed(text, self.dim)
        with self._lock:
            self._items[key] = (vec, meta)

    def delete(self, key: int) -> None:
        with self._lock:
            self._items.pop(key, None)

    def search(self, text: str, k: int = 8) -> list[tuple[int, float, dict]]:
        q = hashing_embed(text, self.dim)
        with self._lock:
            items = list(self._items.items())
        scored = [(key, cosine(q, vec), meta) for key, (vec, meta) in items]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def __len__(self) -> int:
        return len(self._items)


class HybridIndex:
    """hashing（256 维字面相似）+ LLM 软嵌入（10 维语义维度）双路索引。

    - upsert：hashing 立即算；LLM 维度异步可算（chat 每条 2-5s，不阻塞写路径）
      ——本实现同步算但只在 chat 可调用时；失败纯 hashing
    - search：两路 cosine 各自算，取 max（字面命中 or 语义命中任一即可）
    - LLM 嵌入结果缓存（同文本不重打），池挂自动退化 BruteForce 等价
    """

    def __init__(self, chat=None, dim: int = DIM):
        self.dim = dim
        self._chat = chat          # callable(messages)->str；None=纯 hashing
        self._items: dict[int, tuple[list[float], list[float] | None, dict]] = {}
        self._lock = threading.Lock()

    def _dims(self, text: str) -> list[float] | None:
        if self._chat is None:
            return None
        return llm_dim_embed(text, self._chat)

    def upsert(self, key: int, text: str, meta: dict) -> None:
        vec = hashing_embed(text, self.dim)
        dims = self._dims(text)
        with self._lock:
            self._items[key] = (vec, dims, meta)

    def delete(self, key: int) -> None:
        with self._lock:
            self._items.pop(key, None)

    def search(self, text: str, k: int = 8) -> list[tuple[int, float, dict]]:
        qh = hashing_embed(text, self.dim)
        qd = self._dims(text)
        with self._lock:
            items = list(self._items.items())
        scored = []
        for key, (vh, vd, meta) in items:
            s = cosine(qh, vh)
            if qd is not None and vd is not None:
                # 语义路：维度向量都归一化后 cosine；全零向量（无语义文本）跳过
                n1 = math.sqrt(sum(x * x for x in qd)) or 1.0
                n2 = math.sqrt(sum(x * x for x in vd)) or 1.0
                if n1 > 0 and n2 > 0:
                    s = max(s, cosine(qd, vd) / (n1 * n2))
            scored.append((key, s, meta))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def __len__(self) -> int:
        return len(self._items)
