"""注入器：把沉淀下来的记忆**选择性**喂给主模型——软引导，不替它回答，也不改人格。

产出是一段带来源标注的文本块 + 结构化条目（便于调用方自己再筛）：

    [记忆#12 · wechat/work · 2026-10-28 · 置信0.90] 用户住在杭州
    关系：张伟 —(同事)→ 用户
    表达风格（软引导）：语气=口语化、长度=简短

三条硬约束（对应用户的要求）：
- **选择性**：按相关度/置信度打分，受 `max_items` 与 `max_chars` 双预算约束，超了就丢。
- **来源标注**：每条都带 id / 平台 / scope / 时间 / 置信度，主模型能区分"记忆"与"本轮输入"。
- **不越权**：`persona_touched=False`、`generated_reply=None`、`soft=True`；风格只作为提示，
  永远不会被写成"你是……"这类人格设定。

召回本身仍然是 100ms 硬预算的那条路，注入只是在它的结果上加格式与预算。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .db import DB
from .recall import Recaller, _overlap
from .store import (
    Ident,
    bump_usage,
    latest_summary,
    log_injection,
    profile_snapshot,
    style_snapshot,
)
from . import params
from . import store

HEADER = "以下是与本次问题相关的已知记忆（软引导：可忽略，不要当成用户本轮说的话，不要复述标注）："
FOOTER = "以上记忆若与用户本轮陈述冲突，以用户本轮陈述为准，并把它当作更正记下来。"
STYLE_HEADER = "表达风格参考（软引导，只影响表达方式，不构成人格设定）："
# 弃答档位（手册 v5 四档的最小实现）：不是让调用方"少说话"，而是让它可以诚实。
TIER_HINT = {
    "stale": "· 上次说的，可能已过时",
    "vague": "· 只沾到边，不确定",
}


@dataclass(slots=True)
class InjectItem:
    kind: str  # fact | knowledge | relation | profile | summary | style
    id: int
    text: str
    score: float
    label: str = ""
    tier: str = "sure"  # sure | vague | stale（弃答四档里 applicable 的三档）
    rel: float = 1.0    # 相关度（用于弃答门：太不相关的不硬凑）

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "text": self.text,
                "score": round(self.score, 3), "label": self.label, "tier": self.tier,
                "rel": round(self.rel, 3)}


@dataclass(slots=True)
class Injection:
    query: str
    text: str
    items: list[InjectItem] = field(default_factory=list)
    budget_chars: int = 700
    took_ms: float = 0.0
    dropped: int = 0
    injection_id: int = 0
    soft: bool = True
    persona_touched: bool = False
    generated_reply: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "text": self.text,
            "items": [i.to_dict() for i in self.items],
            "budget_chars": self.budget_chars,
            "took_ms": round(self.took_ms, 3),
            "dropped": self.dropped,
            "injection_id": self.injection_id,
            "soft": self.soft,
            "persona_touched": self.persona_touched,
            "generated_reply": self.generated_reply,
        }


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or 0))


class Injector:
    def __init__(self, db: DB, recaller: Recaller, *, max_chars: int = 700, max_items: int = 12,
                 min_rel: float = 0.0):
        self.db = db
        self.recaller = recaller
        self.max_chars = max_chars
        self.max_items = max_items
        # 弃答下限：与查询的 2-gram 重叠低于这个值就不进 prompt（0=关掉这道门）
        self.min_rel = float(min_rel)

    def build(
        self,
        query: str,
        ident: Ident,
        *,
        session: str | None = None,
        max_chars: int | None = None,
        max_items: int | None = None,
        mode: str = "exact",
        log: bool = True,
        learn: bool = True,
    ) -> Injection:
        t0 = time.perf_counter()
        budget = self.max_chars if max_chars is None else max_chars
        cap = self.max_items if max_items is None else max_items
        conn = self.db.conn()

        items: list[InjectItem] = []
        res = self.recaller.recall(
            query, ident, session=session, top_k=cap, mode=mode,
        )
        hit_ids = [h.id for h in res.hits if h.kind in ("fact", "semantic")]
        kinds: dict[int, tuple[str, float, str]] = {}
        if hit_ids:
            q = ",".join("?" * len(hit_ids))
            now_ts = time.time()
            for r in conn.execute(
                # status 过滤：janitor 软删的 fact 绝不能进 prompt（读路径一致性）
                f"SELECT id, kind, confidence, updated_at FROM fact"
                f" WHERE id IN ({q}) AND status='active'", tuple(hit_ids)
            ).fetchall():
                age_d = (now_ts - float(r["updated_at"] or now_ts)) / 86400.0
                conf = float(r["confidence"])
                tier = "sure"
                if age_d > params.STALE_TIER_DAYS:
                    tier = "stale"
                elif conf < 0.6:
                    tier = "vague"
                kinds[int(r["id"])] = (r["kind"] or "fact", conf, tier)
        for h in res.hits:
            if h.kind not in ("fact", "semantic"):
                continue
            meta = h.meta or {}
            kind, conf, tier = kinds.get(h.id, ("fact", float(meta.get("confidence", 0.7)), "vague"))
            hint = TIER_HINT.get(tier, "")
            items.append(InjectItem(
                kind=kind, id=h.id, text=h.text, score=h.score,
                label=f"[记忆#{h.id} · {meta.get('platform', ident.platform)}/{meta.get('scope', ident.scope)}"
                      f" · {_day(time.time())} · 置信{conf:.2f}{hint}]",
                tier=tier,
                rel=float(meta.get("rel", 1.0)),
            ))

        for key, meta in profile_snapshot(conn, ident).items():
            items.append(InjectItem(
                kind="profile", id=0, text=f"{key}：{meta['value']}",
                score=0.9 * float(meta["confidence"]),
                label=f"[画像 · {ident.platform}/{ident.scope} · {_day(meta['updated_at'])}"
                      f" · 置信{meta['confidence']:.2f}]",
            ))

        # 关系边：与查询有字面重叠的优先，其余作为背景低分带出
        w, args = ident.where()
        for r in conn.execute(
            f"SELECT id,subject,predicate,object,confidence,updated_at FROM fact"
            f" WHERE {w} AND kind='relation' AND status='active'"
            f" ORDER BY updated_at DESC LIMIT 12",
            tuple(args),
        ).fetchall():
            a, rel, b = r["subject"], r["predicate"], r["object"]
            b_display = "用户" if b in ("user", "用户") else b
            text = f"{a} —({rel})→ {b_display}"
            rel_score = 0.85 * _overlap(query, text) + 0.2 * float(r["confidence"])
            items.append(InjectItem(
                kind="relation", id=r["id"], text=text, score=rel_score,
                label=f"[关系#{r['id']} · 置信{float(r['confidence']):.2f}]",
            ))

        sm = latest_summary(conn, ident, session)
        if sm:
            items.append(InjectItem(
                kind="summary", id=0, text=sm["text"],
                score=0.6,
                label=f"[摘要 · {sm['session'] or session or '-'} · 至第{sm['upto_turn']}轮"
                      f" · {_day(sm['ts'])}]",
            ))

        # 自我记忆（她自己的事）：**只带与本次查询沾边的**，不做"人设倾泻"。
        # 手册 v5 把它列为第五层，但直接铺进 prompt 会让"她"每轮重复自我介绍，故：
        # 最多 2 条、每条 ≤60 字、必须有 2-gram 重叠、分数低于普通事实（0.45）。
        for sm_row in store.self_memory_list(conn, scope=ident.scope, limit=12):
            txt = str(sm_row.get("text") or "").strip()
            if not txt or len(txt) > 60:
                continue
            if _overlap(query, txt) <= 0.0:
                continue
            items.append(InjectItem(
                kind="self", id=int(sm_row["id"]), text=txt, score=0.45,
                label=f"[自我 · {sm_row.get('kind') or 'pref'} · {_day(sm_row['created_at'])}]",
                rel=float(_overlap(query, txt)),
            ))
            if sum(1 for i in items if i.kind == "self") >= 2:
                break

        # 弃答门（手册 v5 的「none」档）：**连最好的一条都几乎不相关**就整块不出，
        # 而不是逐条砍（逐条砍会把"沾一点边但有信息量"的条目一起砍掉，实测 hit@5 掉 25 点）。
        # 只对 fact/semantic 生效（它们有 rel）；关系边/摘要照旧作背景。
        best_rel = max(
            [i.rel for i in items if i.kind in ("fact", "knowledge", "semantic")] or [1.0])
        if self.min_rel > 0 and best_rel < self.min_rel:
            items = []

        items.sort(key=lambda i: (-i.score, i.kind, i.id))
        style = style_snapshot(conn, ident)
        style_line = ""
        if style:
            kv = "、".join(f"{k}={v['value']}" for k, v in sorted(style.items()))
            style_line = f"{STYLE_HEADER}{kv}"

        # 硬预算按**最终文本**算：header + 事实行 + 风格行 + footer 加起来不许超。
        head_cost = len(HEADER) + 1
        style_cost = len(style_line) + 1 if style_line else 0
        foot_cost = len(FOOTER) + 1
        avail = budget - head_cost - style_cost - foot_cost
        if avail < 80 and style_line:  # 空间不够先砍风格块，保事实
            style_line, style_cost = "", 0
            avail = budget - head_cost - foot_cost

        body: list[str] = []
        kept: list[InjectItem] = []
        used = 0
        dropped = 0
        for it in items:
            if len(kept) >= cap:
                dropped += 1
                continue
            line = f"{it.label} {it.text}"
            if used + len(line) + 1 > avail:
                dropped += 1
                continue
            body.append(line)
            kept.append(it)
            used += len(line) + 1

        parts = [HEADER]
        parts.extend(body)
        if style_line:
            parts.append(style_line)
            kept.append(InjectItem(kind="style", id=0, text=style_line[len(STYLE_HEADER):],
                                   score=1.0, label="[风格·软引导]"))
        if body:
            parts.append(FOOTER)
        text = "\n".join(parts) if body else ""

        inj = Injection(query=query, text=text, items=kept, budget_chars=budget,
                        took_ms=(time.perf_counter() - t0) * 1000, dropped=dropped)
        if log and text:
            with self.db.tx() as c:
                inj.injection_id = log_injection(
                    c, ident, query, [i.id for i in kept if i.id], text, session=session
                )
                # 读→强化（borrow-plan P0-1）：被注入=被想起一次。只记计数与稳定性，
                # 不参与排序；REVIEW_COOLDOWN_S 内重复只算一次（store.bump_usage 内判）。
                if learn:
                    bump_usage(c, ident, [i.id for i in kept if i.id and i.kind != "relation"])
        return inj


__all__ = ["HEADER", "FOOTER", "InjectItem", "Injection", "Injector", "STYLE_HEADER"]
