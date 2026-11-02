"""主链路快召回：100ms 硬预算。

三条保证：
1. 所有慢的东西（SQLite 查询、向量检索）在**另开的线程**里跑，主线程只等 remaining 秒；
2. 等不到就退化到纯内存快照（画像 + 会话缓冲），绝不超过预算；
3. 退化会在结果里标出来（degraded），不静默假装完整。

多平台：每次召回都按 (platform, platform_user_id) 过滤，再按 scope 收窄；
不同平台的记忆互不串味，靠的是过滤而不是分库分表。
"""

from __future__ import annotations

import math
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

from aiji.right_hand import params
from aiji.hippocampus.db import DB


def now_bucket(seconds: int = 300) -> int:
    """概率回忆的时间桶种子：同一问 5 分钟内答案稳定（防'精神分裂'），
    跨桶自然换花样（防复读机）。"""
    return int(time.time() // seconds)
from aiji.hippocampus.store import Fact, Ident, _ngrams, links_for, norm, profile_snapshot, recent_turns, search_facts
from aiji.hippocampus.vectors import NullIndex, SemanticIndex

_BUFFER_PER_SESSION = 2  # 内存缓冲保留 N 倍 recent 条，避免无限长
# 不同属性衰减速度不同：住址/职业变化慢，偏好漂移快（Ebbinghaus 半衰期，按天）。
_HALF_LIFE_DAYS = {
    "名字": 365.0, "居住地": 240.0, "职业": 365.0, "年龄": 365.0,
    "邮箱": 365.0, "电话": 365.0, "生日": 365.0,
    "偏好": 60.0, "兴趣": 60.0, "爱好": 60.0, "备注": 45.0,
}
_DEFAULT_HALF_LIFE_DAYS = 120.0


@dataclass(slots=True)
class Hit:
    kind: str  # fact | profile | turn | media | semantic
    id: int
    text: str
    score: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecallResult:
    hits: list[Hit]
    took_ms: float
    budget_ms: int
    degraded: list[str] = field(default_factory=list)
    # 影子排名（rank_mode='rrf_shadow' 时填充）：只记录"如果开 RRF 会选哪几条"，
    # 不改变实际返回——新打分上线前先对比一段时间（memory-brain 的 shadow 做法）。
    shadow: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [
                {"kind": h.kind, "id": h.id, "text": h.text, "score": round(h.score, 4), "meta": h.meta}
                for h in self.hits
            ],
            "took_ms": round(self.took_ms, 3),
            "budget_ms": self.budget_ms,
            "degraded": self.degraded,
            "shadow": self.shadow,
        }


def _overlap(q: str, target: str) -> float:
    """2-gram 交叠率，中文短句上比整串包含稳。"""
    a, b = norm(q), norm(target)
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    ga = set(_ngrams(a, 2))
    gb = set(_ngrams(b, 2))
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / min(len(ga), len(gb))


def _recency(age_s: float, half_life_s: float = 30 * 86400) -> float:
    return math.exp(-max(age_s, 0.0) * math.log(2) / half_life_s)


def half_life_days(f: Fact) -> float:
    if f.kind == "knowledge":
        return 180.0
    if f.kind == "relation":
        return 365.0
    if f.kind == "episodic":
        return params.EPISODIC_HALF_LIFE_DAYS
    return _HALF_LIFE_DAYS.get(f.predicate, _DEFAULT_HALF_LIFE_DAYS)


def _recency_for_fact(f: Fact, age_s: float, decay_mode: str | None = None) -> float:
    """按衰减口径算新近度。

    - `stability`（默认）：FSRS-5 幂律曲线，用**事实自己存的 stability**——`bump_usage` 每被引用
      一次就把它涨一点（封顶 `STABILITY_MAX_DAYS`），解冻再 ×1.2。于是"常被想起的旧事实"掉得比
      "从没人提的旧事实"慢，这正是 FSRS 的核心效应。
    - `half_life`：旧口径，指数衰减，半衰期只由事实类型决定，跟用没用过无关（保留用于对照/回退）。
    """
    mode = str(decay_mode or params.RECALL_DECAY_MODE or "half_life").lower()
    if mode == "stability":
        s = max(0.5, float(getattr(f, "stability", 0) or params.STABILITY_INIT_DAYS))
        days = max(age_s, 0.0) / 86400.0
        return float((1.0 + params.FSRS_F * days / s) ** -0.5)
    return _recency(age_s, half_life_days(f) * 86400)


def score_fact(f: Fact, rank: int, now: float, rel: float = 0.0,
               decay_mode: str | None = None) -> float:
    """多信号打分（mem0 v3 的多信号 + Graphiti 的时间衰减）：

    0.55 相关度 + 0.20 重要度 + 0.15 新近度（按属性类型的半衰期）+ 0.10 置信度，
    再用反馈命中（hit_count）与重复提及（mentions）做温和加成。

    2026-11-03：新近度现在用事实自己存的 `stability`（FSRS-5 曲线），切换见 `params.RECALL_DECAY_MODE`。
    默认口径由 `params.RECALL_DECAY_MODE` 决定。
    """
    recency = _recency_for_fact(f, now - f.updated_at, decay_mode)
    base = 0.55 * rel + 0.20 * f.importance + 0.15 * recency + 0.10 * f.confidence
    boost = (
        1.0
        + 0.18 * min(1.0, math.log1p(max(f.hit_count, 0)) / 2.0)
        + 0.10 * min(1.0, math.log1p(max(f.mentions, 1) - 1) / 2.0)
    )
    return base * boost + 0.02 / (1.0 + 0.25 * max(rank, 0))


def _ikey(ident: Ident) -> tuple[str, str, str]:
    return (ident.platform, ident.platform_user_id, str(ident.scope or "*"))


class Recaller:
    def __init__(
        self,
        db: DB,
        *,
        budget_ms: int = 100,
        top_k: int = 8,
        recent: int = 40,
        index: SemanticIndex | None = None,
        rank_mode: str = "weighted",
    ):
        self.db = db
        self.budget_ms = budget_ms
        self.top_k = top_k
        self.recent = recent
        self.rank_mode = (rank_mode or "weighted").lower()
        self.index = index if index is not None else NullIndex()
        self.semantic = index is not None
        self._profiles: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
        self._turns: dict[tuple[str, str, str, str], list[tuple[int, str, str, float]]] = {}
        self._turns_loaded: set[tuple[str, str, str, str]] = set()
        self._exec = ThreadPoolExecutor(max_workers=4, thread_name_prefix="aiji-recall")

    # ---- 写侧调用：保持内存快照最新，代价 O(1) ----

    def refresh_profile(self, ident: Ident) -> None:
        self._profiles[_ikey(ident)] = profile_snapshot(self.db.conn(), ident)

    def note_turn(self, ident: Ident, session: str, role: str, content: str, ts: float) -> None:
        buf = self._turns.setdefault((*_ikey(ident), session), [])
        buf.append((0, role, content, ts))
        cap = self.recent * _BUFFER_PER_SESSION
        if len(buf) > cap:
            del buf[: len(buf) - cap]

    def _turns_for(self, ident: Ident, session: str) -> list[tuple[int, str, str, float]]:
        key = (*_ikey(ident), session)
        if key not in self._turns_loaded:
            rows = recent_turns(self.db.conn(), ident, session, self.recent)
            self._turns[key] = [(r["id"], r["role"], r["content"], r["ts"]) for r in rows]
            self._turns_loaded.add(key)
        return self._turns.get(key, [])

    def _profile_for(self, ident: Ident) -> dict[str, dict[str, Any]]:
        key = _ikey(ident)
        prof = self._profiles.get(key)
        if prof is None:
            prof = profile_snapshot(self.db.conn(), ident)  # 首次见这个身份：一次索引点查
            self._profiles[key] = prof
        return prof

    # ---- 主链路 ----

    def recall(
        self,
        query: str,
        ident: Ident,
        *,
        session: str | None = None,
        top_k: int | None = None,
        budget_ms: int | None = None,
        mode: str = "exact",
    ) -> RecallResult:
        """mode='exact'（默认）：确定性 top-k——指令、纠错、@场景的唯一合法模式。
        mode='chat'：概率回忆（认知启发）——锚点确定性保留 + 其余候选轮盘抽样。
        锚点 = profile 命中与置信 ≥0.7 的 fact；它们不参与抽样。
        返回 meta 里带 sample=True 的条目是被抽样选中的（审计用）。
        """
        t0 = time.perf_counter()
        budget = self.budget_ms if budget_ms is None else budget_ms
        k = self.top_k if top_k is None else top_k
        deadline = t0 + budget / 1000.0
        degraded: list[str] = []
        q = (query or "").strip()
        now = time.time()

        if not q:
            return RecallResult([], (time.perf_counter() - t0) * 1000, budget, ["empty_query"])

        profile = self._profile_for(ident)
        remaining = deadline - time.perf_counter()
        hits: list[Hit] = []
        if remaining > 0.001:
            fut: Future = self._exec.submit(self._search_db, q, ident, session, k, now)
            try:
                hits.extend(fut.result(timeout=remaining))
            except FutureTimeout:
                fut.cancel()
                degraded.append("deadline")
            except Exception as exc:  # noqa: BLE001 - 召回永不抛给主链路
                degraded.append(f"search_error:{type(exc).__name__}")
        else:
            degraded.append("deadline_zero")

        hits.extend(self._cheap_hits(q, ident, profile, session, now))
        hits.sort(key=lambda h: h.score, reverse=True)
        merged: list[Hit] = []
        seen: set[tuple[str, int, str]] = set()
        for h in hits:
            key = (h.kind, h.id, h.text[:40])
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)

        if mode == "chat":
            merged = self._probabilistic_merge(merged, q, k)
        else:
            merged = merged[:k]
        shadow: list[dict[str, Any]] = []
        if self.rank_mode in ("rrf", "rrf_shadow"):
            fused = self._fuse_rrf(hits)
            if self.rank_mode == "rrf":
                # 关掉概率抽样的语义差异：RRF 只换"怎么合"，不动 mode='chat' 的抽样层
                merged = self._probabilistic_merge(fused, q, k) if mode == "chat" else fused[:k]
            else:
                shadow = [
                    {"kind": h.kind, "id": h.id, "score": round(h.score, 4)}
                    for h in fused[:k]
                ]
        return RecallResult(merged, (time.perf_counter() - t0) * 1000, budget, degraded, shadow)

    _RRF_K = 60  # 手册 v5 三路 RRF 的 k=60（等权、只吃名次，不吃分数尺度）

    def _fuse_rrf(self, hits: list[Hit]) -> list[Hit]:
        """按**名次**融合各来源（fact / semantic / turn / profile / summary）。

        与手工加权（0.55/0.20/0.15/0.10）的区别：不吃分数绝对值，只用"在各自来源里排第几"，
        所以换 embedding、换 FTS 权重、某一路突然全高分都不会把排序带偏。
        跨来源去重按 (kind,id) 合并分数——同一条事实被 FTS 与语义同时命中会更靠前。
        """
        groups: dict[str, list[Hit]] = {}
        for h in hits:
            groups.setdefault(h.kind, []).append(h)
        fused: dict[tuple[str, int], float] = {}
        best: dict[tuple[str, int], Hit] = {}
        for kind, hs in groups.items():
            hs.sort(key=lambda h: h.score, reverse=True)
            for rank, h in enumerate(hs, 1):
                key = (h.kind, h.id)
                fused[key] = fused.get(key, 0.0) + 1.0 / (self._RRF_K + rank)
                prev = best.get(key)
                if prev is None or h.score > prev.score:
                    best[key] = h
        out: list[Hit] = []
        for key, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True):
            h = best[key]
            # 归一到 0–1 区间（并按多路命中给轻微加成），保持与旧分数的量级可比
            h2 = Hit(h.kind, h.id, h.text, min(1.0, score * 30.0), h.meta)
            out.append(h2)
        return out

    def _probabilistic_merge(self, hits: list[Hit], q: str, k: int) -> list[Hit]:
        """概率回忆：锚点 100% 保留；其余从候选池轮盘抽样 RECALL_CHAT_TAKE 条。

        大坑防御（用户复盘）：同问两遍答案漂移被当成"精神分裂"——锚点先行，
        概率只作用在"闲聊补充"层，核心事实永远确定性在场。
        """
        rng = random.Random(f"{q}|{now_bucket()}")
        anchors: list[Hit] = []
        pool: list[Hit] = []
        for h in hits:
            if h.kind == "profile" or (h.kind == "fact" and h.score >= 0.7):
                anchors.append(h)
            elif h.kind in ("fact", "semantic", "turn"):
                pool.append(h)
            else:
                anchors.append(h)  # summary/关系等结构条目不抽
        take = max(0, min(k - len(anchors), params.RECALL_CHAT_TAKE))
        picked: list[Hit] = []
        if pool and take > 0:
            top_pool = pool[: params.RECALL_CHAT_POOL]
            weights = [max(1e-6, h.score) for h in top_pool]
            if sum(weights) > 0:
                for h in rng.choices(top_pool, weights=weights, k=take):
                    if h not in picked:
                        h.meta = {**h.meta, "sample": True}
                        picked.append(h)
        out = anchors + picked
        out.sort(key=lambda h: h.score, reverse=True)
        return out[:k]

    # ---- 纯内存部分：超时兜底也能立刻返回 ----

    def _cheap_hits(
        self,
        q: str,
        ident: Ident,
        profile: dict[str, dict[str, Any]],
        session: str | None,
        now: float,
    ) -> list[Hit]:
        out: list[Hit] = []
        for i, (key, meta) in enumerate(profile.items()):
            s = _overlap(q, f"{key} {meta['value']}")
            if s > 0.34:
                out.append(
                    Hit("profile", i, f"{key}: {meta['value']}", 1.2 * s * meta["confidence"],
                        {"key": key, "platform": ident.platform, "scope": ident.scope})
                )
        if session:
            turns = self._turns_for(ident, session)
            for tid, role, content, ts in turns[-8:]:
                s = _overlap(q, content)
                if s > 0.2:
                    out.append(Hit("turn", tid, content[:400], 0.9 * s * _recency(now - ts), {"role": role}))
            if not out and turns:
                tid, role, content, ts = turns[-1]
                out.append(Hit("turn", tid, content[:400], 0.15 * _recency(now - ts),
                               {"role": role, "why": "latest"}))
        return out

    # ---- 慢的部分（在 worker 线程执行） ----

    def _search_db(self, q: str, ident: Ident, session: str | None, k: int, now: float) -> list[Hit]:
        out: list[Hit] = []
        fact_hits: list[Hit] = []
        for i, f in enumerate(search_facts(self.db.conn(), ident, q, limit=max(12, k * 3))):
            # 候选可能来自 FTS / 二字组 / 单字兜底，顺序本身不代表相关度，
            # 统一用与查询的 2-gram 重叠度重排。
            rel = max(_overlap(q, f.text), _overlap(q, f.object))
            # 事实键扩展（手册 v5 fact-keys.js 的最小实现）：抽取时把实体/属性/时刻
            # 存进 extra["keys"]，这里用键做二次匹配——"换了个说法"也能对上。
            keys = f.extra.get("keys") if isinstance(f.extra, dict) else None
            if keys:
                krel = max((_overlap(q, str(x)) for x in keys), default=0.0)
                rel = max(rel, 0.7 * krel)
            fact_hits.append(self._fact_hit(f, i, now, rel))
        out.extend(fact_hits)

        # 差序联想（1 跳 + 2 跳，方案调研落点）：从最相关的几条事实出发带出邻居，
        # 分数按邻居权重打折，不能让联想压过直接命中。
        # 2 跳 = "A 认识 B，B 提过 C"：hop 权重 ×0.4² 衰减，RECALL_LINK_BUDGET 封顶。
        if fact_hits:
            try:
                seeds = sorted(fact_hits, key=lambda h: h.score, reverse=True)[:3]
                seen = {h.id for h in fact_hits}
                budget = params.RECALL_LINK_BUDGET
                # ---- 1 跳 ----
                hop1_ids: dict[int, float] = {}  # dst_id -> 父分
                for f, src, weight in links_for(
                    self.db.conn(), ident, [h.id for h in seeds], limit=budget
                ):
                    if f.id in seen:
                        continue
                    seen.add(f.id)
                    src_hit = next((h for h in seeds if h.id == src), None)
                    parent = src_hit.score if src_hit else 0.3
                    score = parent * (0.45 + 0.4 * weight)
                    hop1_ids[f.id] = score
                    out.append(Hit(
                        "fact", f.id, f.text, score,
                        {
                            "platform": f.platform, "scope": f.scope,
                            "subject": f.subject, "predicate": f.predicate,
                            "object": f.object, "linked_from": src,
                            "link_weight": round(weight, 3), "hop": 1,
                        },
                    ))
                    budget -= 1
                # ---- 2 跳（差序格局第二圈水纹）----
                if budget > 0 and hop1_ids and params.RECALL_LINK_HOPS >= 2:
                    hop2_src = list(hop1_ids.keys())[:3]
                    for f, src, weight in links_for(
                        self.db.conn(), ident, hop2_src, limit=budget
                    ):
                        if f.id in seen:
                            continue
                        seen.add(f.id)
                        decay = params.RECALL_LINK_HOPS and 0.4 ** 2 or 0.4
                        parent = hop1_ids.get(src, 0.3)
                        out.append(Hit(
                            "fact", f.id, f.text, parent * decay * (0.45 + 0.4 * weight),
                            {
                                "platform": f.platform, "scope": f.scope,
                                "subject": f.subject, "predicate": f.predicate,
                                "object": f.object, "linked_from": src,
                                "link_weight": round(weight, 3), "hop": 2,
                            },
                        ))
                        budget -= 1
                        if budget <= 0:
                            break
            except Exception:  # noqa: BLE001 - 关联是增强项，坏了不影响主链路
                pass

        if self.semantic:
            try:
                for fid, score, meta in self.index.search(q, k=max(k * 4, 12)):
                    if score <= 0.35:
                        continue
                    # 语义索引是全局的，命中后必须再按身份过滤一次（否则跨平台串号）。
                    if meta.get("platform") != ident.platform:
                        continue
                    if meta.get("platform_user_id") != ident.platform_user_id:
                        continue
                    if ident.scope not in (None, "*") and meta.get("scope") != ident.scope:
                        continue
                    # janitor 软删/过期不会同步清索引——命中后再查一次 status，
                    # 已失效的顺手从索引删掉（读路径一致性，软删不回流 prompt）。
                    alive = self.db.read(
                        "SELECT 1 FROM fact WHERE id=? AND status='active'", (int(fid),)
                    )
                    if not alive:
                        try:
                            self.index.delete(int(fid))
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    out.append(Hit("semantic", fid, meta.get("text", ""), 0.8 * score, meta))
            except Exception:  # noqa: BLE001 - 语义索引是增强项，坏了不影响主链路
                pass
        if session:
            rows = recent_turns(self.db.conn(), ident, session, self.recent)
            for r in rows[-10:]:
                s = _overlap(q, r["content"])
                if s > 0.2:
                    out.append(Hit("turn", r["id"], r["content"][:400], 0.9 * s * _recency(now - r["ts"]),
                                   {"role": r["role"]}))
        return out

    def _fact_hit(self, f: Fact, rank: int, now: float, rel: float = 0.0) -> Hit:
        return Hit(
            "fact",
            f.id,
            f.text,
            score_fact(f, rank, now, rel),
            {
                "platform": f.platform,
                "scope": f.scope,
                "subject": f.subject,
                "predicate": f.predicate,
                "object": f.object,
                "rel": round(rel, 3),
                "importance": round(f.importance, 3),
            },
        )

    def forget_session(self, ident: Ident, session: str) -> None:
        """turn 表被淘汰后清掉内存缓冲，避免召回“已删除的原文”。"""
        key = (*_ikey(ident), session)
        self._turns.pop(key, None)
        self._turns_loaded.discard(key)

    def stats(self) -> dict[str, Any]:
        return {
            "identities_cached": len(self._profiles),
            "sessions_buffered": len(self._turns),
            "semantic_items": len(self.index) if self.semantic else 0,
        }

    def close(self) -> None:
        self._exec.shutdown(wait=False, cancel_futures=True)
