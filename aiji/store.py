"""结构化存储：身份、事实（含冲突消解）、画像、会话轮次、媒体引用、失败队列。

一条数据属于哪个用户，由 (platform, platform_user_id) 决定；scope 只是在同一身份下
再分命名空间（personal / work / group:xxx）。平台特有字段进 extra（JSON 文本），
不往主列里加。媒体只记引用，不存文件。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import params

_SPACE = re.compile(r"\s+")
_TAIL = re.compile(r"[，。！？、；：,.!?;:\"'”）)】\s]+$")
# 中文问句里的虚词：做候选字时要剔掉，否则"我住哪"会退化成全表扫
_STOP = set("我你他她它的了吗呢吧啊呀是在有和与或哪什么怎如何多少请问想能会要这那个们就也都还把被给对从到")


def norm(s: str) -> str:
    return _SPACE.sub("", s or "").strip().lower()


@dataclass(frozen=True, slots=True)
class Ident:
    """身份三元组。身份键是 (platform, platform_user_id)，scope 不参与身份。"""

    platform: str = "local"
    platform_user_id: str = "default"
    scope: str | None = "default"

    @classmethod
    def coerce(cls, obj: Any = None, *, platform: str | None = None,
               platform_user_id: str | None = None, scope: str | None = None) -> "Ident":
        """接受 Ident / (platform, user[, scope]) / dict / 关键字，统一成一个对象。"""
        if isinstance(obj, Ident):
            base = obj
        elif isinstance(obj, Mapping):
            base = cls(
                platform=str(obj.get("platform") or "local"),
                platform_user_id=str(obj.get("platform_user_id") or obj.get("user_id") or "default"),
                scope=obj.get("scope", "default"),
            )
        elif isinstance(obj, (tuple, list)):
            if len(obj) < 2:
                raise ValueError("身份至少要 (platform, platform_user_id)")
            base = cls(str(obj[0]), str(obj[1]), obj[2] if len(obj) > 2 else "default")
        elif obj is None:
            base = cls()
        else:
            raise TypeError(f"无法识别的身份: {type(obj).__name__}")
        return cls(
            platform if platform is not None else base.platform,
            platform_user_id if platform_user_id is not None else base.platform_user_id,
            scope if scope is not None else base.scope,
        )

    def where(self, alias: str = "") -> tuple[str, list[str]]:
        """生成身份过滤 SQL；scope 为 None 或 '*' 表示跨 scope。"""
        p = f"{alias}." if alias else ""
        sql = f"{p}platform=? AND {p}platform_user_id=?"
        args = [self.platform, self.platform_user_id]
        if self.scope not in (None, "*"):
            sql += f" AND {p}scope=?"
            args.append(str(self.scope))
        return sql, args


@dataclass(slots=True)
class FactDraft:
    subject: str
    predicate: str
    object: str
    text: str
    confidence: float = 0.7
    kind: str = "fact"  # fact | knowledge | relation
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Fact:
    id: int
    platform: str
    platform_user_id: str
    scope: str
    subject: str
    predicate: str
    object: str
    text: str
    confidence: float
    status: str
    created_at: float
    updated_at: float
    source_kind: str
    kind: str = "fact"
    hit_count: int = 0
    importance: float = 0.5
    mentions: int = 1
    valid_from: float | None = None
    valid_to: float | None = None
    # 代谢层（v11，borrow-plan P0-2）：stability 是"记忆强度"（天），frozen=1 表示不注入但仍可查。
    stability: float = 7.0
    difficulty: float = 5.0
    frozen: int = 0
    last_used_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _fact(row: sqlite3.Row) -> Fact:
    keys = row.keys()
    try:
        extra = json.loads(row["extra"] or "{}")
    except ValueError:
        extra = {}
    return Fact(
        id=row["id"],
        platform=row["platform"],
        platform_user_id=row["platform_user_id"],
        scope=row["scope"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        text=row["text"],
        confidence=row["confidence"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source_kind=row["source_kind"],
        kind=row["kind"] if "kind" in keys else "fact",
        hit_count=row["hit_count"] if "hit_count" in keys else 0,
        importance=row["importance"] if "importance" in keys else 0.5,
        mentions=row["mentions"] if "mentions" in keys else 1,
        valid_from=row["valid_from"] if "valid_from" in keys else None,
        valid_to=row["valid_to"] if "valid_to" in keys else None,
        stability=float(row["stability"]) if "stability" in keys else 7.0,
        difficulty=float(row["difficulty"]) if "difficulty" in keys else 5.0,
        frozen=int(row["frozen"]) if "frozen" in keys else 0,
        last_used_at=row["last_used_at"] if "last_used_at" in keys else None,
        extra=extra,
    )


def ensure_account(
    conn: sqlite3.Connection,
    ident: Ident,
    *,
    display_name: str | None = None,
    extra: dict[str, Any] | None = None,
    now: float | None = None,
) -> None:
    t = time.time() if now is None else now
    conn.execute(
        "INSERT INTO account(platform,platform_user_id,scope,display_name,extra,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?)"
        " ON CONFLICT(platform,platform_user_id) DO UPDATE SET"
        " display_name=COALESCE(excluded.display_name, account.display_name),"
        " scope=excluded.scope,"
        " extra=CASE WHEN excluded.extra='{}' THEN account.extra"
        " ELSE json_patch(account.extra, excluded.extra) END,"
        " updated_at=excluded.updated_at",
        (
            ident.platform,
            ident.platform_user_id,
            str(ident.scope or "default"),
            display_name,
            json.dumps(extra or {}, ensure_ascii=False),
            t,
            t,
        ),
    )


# ---- 冲突消解：ADD-only 优先 + 单值属性才覆盖 + 否定失效 ----
# 借鉴 mem0 v3：多值属性（偏好/兴趣/备注）累积，不互相覆盖；只有真正互斥的
# 单值属性（居住地/职业/年龄…）才 supersede。借鉴 Graphiti：失效只写 valid_to，不删行。
_PREDICATE_ALIASES = {
    "称呼": "昵称",
    "称呼偏好": "昵称",
    "别名": "昵称",
}
_STABLE_PREDICATES = {
    "名字", "昵称", "居住地", "年龄", "职业", "邮箱", "电话", "生日", "性别",
}
_IMPORTANCE_BASE = {
    "名字": 0.95, "电话": 0.95, "居住地": 0.9, "邮箱": 0.9,
    "年龄": 0.85, "职业": 0.85, "生日": 0.85,
}
LINK_MIN_SIM = 0.35


def base_importance(draft: FactDraft) -> float:
    """写入时重要度：身份事实 > 关系/知识 > 随口偏好；置信度占三成。
    episodic（情景记忆）基线压低：鲜活但轻，冲突时永远让位于结构化事实。"""
    if draft.kind == "relation":
        base = 0.7
    elif draft.kind == "knowledge":
        base = 0.6
    elif draft.kind == "episodic":
        base = 0.35
    else:
        base = _IMPORTANCE_BASE.get(norm(draft.predicate), 0.5)
    return round(max(0.05, min(1.0, 0.7 * base + 0.3 * draft.confidence)), 4)


def _jaccard2(a: str, b: str) -> float:
    """2-gram Jaccard：中文短句上的确定性相似度（无模型可用）。"""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga = set(_ngrams(a, 2)) or {a}
    gb = set(_ngrams(b, 2)) or {b}
    return len(ga & gb) / len(ga | gb)


def _same_value(a: str, b: str) -> bool:
    """同一个值：完全相同，或短的是长的子串且占比 ≥0.5（杭州 / 杭州市）。"""
    a, b = norm(a), norm(b)
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 2 and short in long_ and len(short) / len(long_) >= 0.5


def plan_reconciliation(
    draft: FactDraft, rows: Sequence[sqlite3.Row]
) -> tuple[str, list[int], str]:
    """决定这次写入怎么处理：add | merge | supersede | expire | noop。"""
    if draft.extra.get("negated"):
        targets = [int(r["id"]) for r in rows if _same_value(r["object"], draft.object)]
        if not targets and len(rows) == 1:  # "我不再喜欢猫了" 没抽到宾语时，唯一旧值也失效
            targets = [int(rows[0]["id"])]
        if targets:
            return "expire", targets, "negated_statement"
        return "noop", [], "negation_without_target"
    same = [r for r in rows if _same_value(r["object"], draft.object)]
    if same:
        return "merge", [int(same[0]["id"])], "same_value_again"
    if norm(draft.predicate) in _STABLE_PREDICATES and rows:
        return "supersede", [int(r["id"]) for r in rows], "single_valued_conflict"
    return "add", [], "multi_valued" if rows else "new"


def log_fact(
    conn: sqlite3.Connection,
    ident: Ident,
    op: str,
    fact_id: int,
    target_id: int | None = None,
    detail: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO fact_log(platform,platform_user_id,scope,op,fact_id,target_id,detail,ts)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"),
            op, int(fact_id), target_id,
            json.dumps(detail or {}, ensure_ascii=False), time.time() if now is None else now,
        ),
    )


def link_facts(
    conn: sqlite3.Connection,
    ident: Ident,
    fact_id: int,
    subject: str,
    predicate: str,
    text: str,
    *,
    min_sim: float = LINK_MIN_SIM,
    limit: int = 8,
    max_links: int = 3,
    now: float | None = None,
    dropped: list[tuple[float, str]] | None = None,
) -> int:
    """给新事实连上同身份下的近邻（A-MEM 式关联记忆），双向写入。

    只连最相似的 max_links 条，避免事实一多就把关联表撑爆。
    dropped：审计出参（None 时零开销）——阈值之上却因 cap 落选的候选
    (sim, 对方text) 按相似度降序追加进去，用于"触顶丢的是噪声还是语义"的数据判定。
    """
    t = time.time() if now is None else now
    scope = str(ident.scope or "default")
    rows = conn.execute(
        "SELECT id, text, subject FROM fact WHERE platform=? AND platform_user_id=? AND scope=?"
        " AND status='active' AND id<>? AND (subject=? OR predicate=?)"
        " ORDER BY updated_at DESC LIMIT ?",
        (ident.platform, ident.platform_user_id, scope, fact_id, subject, predicate, limit),
    ).fetchall()
    scored: list[tuple[float, int]] = []
    for r in rows:
        sim = _jaccard2(text, r["text"])
        if sim < min_sim:
            continue
        if norm(r["subject"]) == norm(subject):
            sim = min(1.0, sim + 0.1)
        scored.append((sim, int(r["id"])))
    scored.sort(reverse=True)
    if dropped is not None and len(scored) > max_links:
        dropped.extend((s, next(r["text"] for r in rows if int(r["id"]) == d))
                       for s, d in scored[max_links:])
    n = 0
    for sim, dst in scored[:max_links]:
        for a, b in ((fact_id, dst), (dst, fact_id)):
            cur = conn.execute(
                "INSERT OR IGNORE INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, scope, a, b, round(sim, 4),
                 "similar_text", t),
            )
            n += max(0, cur.rowcount)
    return n


def links_for(
    conn: sqlite3.Connection, ident: Ident, src_ids: Sequence[int], limit: int = 6
) -> list[tuple[Fact, int, float]]:
    """1 跳关联：给定事实 id，取同身份下仍然 active 的邻居（带来源与权重）。"""
    ids = [int(i) for i in src_ids if i]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    # 两条硬要求，缺一条这条查询就从 0.3ms 退化到 300ms：
    # 1) 关联表侧也要带身份条件，否则 idx_fact_link_src 用不上，SQLite 会“扫 fact 再全表扫 fact_link”；
    # 2) CROSS JOIN 固定 fl 为外循环（SQLite 会重排普通 JOIN）。
    w, wargs = ident.where("f")
    wl, wargs_l = ident.where("fl")
    rows = conn.execute(
        f"SELECT fl.src_id AS src_id, fl.weight AS weight, f.* FROM fact_link fl"
        f" CROSS JOIN fact f ON f.id=fl.dst_id"
        f" WHERE {wl} AND fl.src_id IN ({q}) AND f.status='active' AND {w}"
        f" ORDER BY fl.weight DESC LIMIT ?",
        (*wargs_l, *ids, *wargs, limit),
    ).fetchall()
    return [(_fact(r), int(r["src_id"]), float(r["weight"])) for r in rows]


def fact_keys(
    subject: str, predicate: str, obj: str, text: str = "", *, max_keys: int = 14
) -> list[str]:
    """事实键（手册 v5 fact-keys.js 的最小实现）：抽取时把可检索的"抓手"存下来。

    组成：三元组本身 + 主语宾语拼接的 2-gram（覆盖"换了个说法"）+ 文本里的数字/时间。
    召回侧用它做二次匹配（recall._search_db），所以键越多召回越宽、但噪声也越多，
    这里封顶 14 个、且只取确定性的部分（不调模型、不加延迟）。
    """
    keys: list[str] = []

    def add(v: str) -> None:
        v = norm(v or "")
        if v and v not in keys:
            keys.append(v)

    for raw in (subject, predicate, obj):
        add(raw)
    for g in _ngrams(norm(f"{subject}{obj}"), 2)[:8]:
        add(g)
    for tok in re.findall(r"\d{1,4}\s*[年月日号岁块元点:：\-]?", text or ""):
        add(tok)
    return keys[:max_keys]


def add_fact(
    conn: sqlite3.Connection,
    ident: Ident,
    draft: FactDraft,
    *,
    source: str = "",
    source_kind: str = "model",
    now: float | None = None,
) -> tuple[int, list[int]]:
    """写入事实并做冲突消解。

    规则（确定性，离线可用）：
      - 同值再说一遍 → merge（置信度取大、mentions+1、文本取长）
      - 多值属性（偏好/兴趣/备注）→ 累积成多条，不覆盖
      - 单值属性（居住地/职业/年龄…）换值 → 旧行 supersede（valid_to + superseded_by）
      - 否定句（extra["negated"]）→ 命中的旧行 expire（valid_to），不新增
    返回 (新/合并的 fact_id, 被失效的旧 id 列表)；否定且无新行时 fact_id=0。
    """
    t = time.time() if now is None else now
    subject = norm(draft.subject)
    predicate = _PREDICATE_ALIASES.get(norm(draft.predicate), norm(draft.predicate))
    obj = norm(draft.object)
    if not subject or not predicate or not obj:
        raise ValueError("事实三元组不能有空项")
    scope = str(ident.scope or "default")
    kind = draft.kind if draft.kind in ("fact", "knowledge", "relation", "episodic") else "fact"
    # 事实键（P1-2）：所有写路径都过 add_fact，所以在这里补最省事、也保证不漏。
    if not (draft.extra or {}).get("keys"):
        draft.extra = dict(draft.extra or {})
        draft.extra["keys"] = fact_keys(subject, predicate, obj, draft.text)
    rows = conn.execute(
        "SELECT id, object, confidence, text, importance FROM fact"
        " WHERE platform=? AND platform_user_id=? AND scope=?"
        " AND subject=? AND predicate=? AND kind=? AND status='active'",
        (ident.platform, ident.platform_user_id, scope, subject, predicate, kind),
    ).fetchall()
    op, targets, reason = plan_reconciliation(draft, rows)
    importance = base_importance(draft)

    if op == "noop":
        log_fact(conn, ident, "noop", 0, None,
                 {"reason": reason, "text": draft.text}, now=t)
        return 0, []

    if op == "expire":
        conn.executemany(
            "UPDATE fact SET status='expired', valid_to=?, updated_at=? WHERE id=?",
            [(t, t, i) for i in targets],
        )
        for i in targets:
            log_fact(conn, ident, "expire", i, None,
                     {"reason": reason, "text": draft.text}, now=t)
        return 0, targets

    if op == "merge":
        target = next(r for r in rows if int(r["id"]) == targets[0])
        text = draft.text if len(draft.text) > len(target["text"]) else target["text"]
        conn.execute(
            "UPDATE fact SET confidence=MAX(confidence, ?), importance=MAX(importance, ?),"
            " mentions=mentions+1, text=?, updated_at=? WHERE id=?",
            (draft.confidence, importance, text, t, target["id"]),
        )
        log_fact(conn, ident, "merge", int(target["id"]), None,
                 {"reason": reason, "text": draft.text}, now=t)
        return int(target["id"]), []

    cur = conn.execute(
        "INSERT INTO fact(platform,platform_user_id,scope,subject,predicate,object,text,"
        "confidence,status,source,source_kind,kind,extra,importance,mentions,valid_from,"
        "created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?, 'active', ?,?,?,?,?,1,?,?,?)",
        (
            ident.platform, ident.platform_user_id, scope, subject, predicate, obj, draft.text,
            draft.confidence, source, source_kind, kind,
            json.dumps(draft.extra or {}, ensure_ascii=False), importance, t, t, t,
        ),
    )
    new_id = int(cur.lastrowid)
    old = [int(r["id"]) for r in rows] if op == "supersede" else []
    if old:
        conn.executemany(
            "UPDATE fact SET status='superseded', superseded_by=?, valid_to=?, updated_at=?"
            " WHERE id=?",
            [(new_id, t, t, i) for i in old],
        )
        for i in old:
            log_fact(conn, ident, "supersede", i, new_id,
                     {"reason": reason, "text": draft.text}, now=t)
    log_fact(conn, ident, "add", new_id, None,
             {"reason": reason, "kind": kind, "importance": importance}, now=t)
    link_facts(conn, ident, new_id, subject, predicate, draft.text, now=t)
    return new_id, old


def upsert_profile(
    conn: sqlite3.Connection,
    ident: Ident,
    key: str,
    value: str,
    *,
    confidence: float = 0.8,
    source_fact_id: int | None = None,
    extra: dict[str, Any] | None = None,
    now: float | None = None,
) -> None:
    t = time.time() if now is None else now
    k = norm(key)[:64]
    if not k or not (value or "").strip():
        return
    conn.execute(
        "INSERT INTO profile(platform,platform_user_id,scope,key,value,confidence,"
        "source_fact_id,extra,updated_at,updates) VALUES(?,?,?,?,?,?,?,?,?,1)"
        " ON CONFLICT(platform,platform_user_id,scope,key) DO UPDATE SET"
        " value=excluded.value,"
        " confidence=MAX(profile.confidence, excluded.confidence),"
        " source_fact_id=excluded.source_fact_id,"
        " extra=excluded.extra, updated_at=excluded.updated_at,"
        " updates=profile.updates+1",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"), k,
            value.strip()[:500], confidence, source_fact_id,
            json.dumps(extra or {}, ensure_ascii=False), t,
        ),
    )


def add_turn(
    conn: sqlite3.Connection,
    ident: Ident,
    session: str,
    role: str,
    content: str,
    *,
    extra: dict[str, Any] | None = None,
    now: float | None = None,
) -> int:
    t = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO turn(platform,platform_user_id,scope,session,role,content,extra,ts)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"),
            session, role, content,
            json.dumps(extra or {}, ensure_ascii=False), t,
        ),
    )
    return int(cur.lastrowid)


def recent_turns(
    conn: sqlite3.Connection, ident: Ident, session: str, limit: int = 40
) -> list[dict[str, Any]]:
    w, args = ident.where()
    rows = conn.execute(
        f"SELECT id, role, content, ts, extra FROM turn WHERE {w} AND session=?"
        " ORDER BY id DESC LIMIT ?",
        (*args, session, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def turn_by_external_id(
    conn: sqlite3.Connection,
    ident: Ident,
    session: str,
    external_id: str,
) -> int | None:
    """Find a previously written platform message by idempotency key.

    The id lives in `turn.extra.message_id` so no schema migration is needed.
    The caller checks inside the same transaction that writes the turn.
    """
    value = str(external_id or "").strip()
    if not value:
        return None
    w, args = ident.where()
    row = conn.execute(
        f"SELECT id FROM turn WHERE {w} AND session=?"
        " AND json_extract(extra, '$.message_id')=? ORDER BY id LIMIT 1",
        (*args, session, value),
    ).fetchone()
    return int(row["id"]) if row else None


def recent_scope_turns(
    conn: sqlite3.Connection,
    platform: str,
    scope: str,
    session: str,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Read a shared conversation scope (for example one QQ group) across users.

    This is intentionally narrower than recall: it only rebuilds the visible
    transcript for one session. Facts/profile/style still use Ident.where and
    remain isolated per platform_user_id.
    """
    rows = conn.execute(
        "SELECT id, platform_user_id, role, content, ts, extra FROM turn"
        " WHERE platform=? AND scope=? AND session=?"
        " ORDER BY id DESC LIMIT ?",
        (platform, scope, session, max(1, int(limit))),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def session_turn_ids(conn: sqlite3.Connection, ident: Ident, session: str) -> list[int]:
    w, args = ident.where()
    return [
        int(r["id"]) for r in conn.execute(
            f"SELECT id FROM turn WHERE {w} AND session=? ORDER BY id", (*args, session)
        ).fetchall()
    ]


def turn_count(conn: sqlite3.Connection, ident: Ident, session: str) -> int:
    w, args = ident.where()
    return int(conn.execute(
        f"SELECT COUNT(*) FROM turn WHERE {w} AND session=?", (*args, session)
    ).fetchone()[0])


def archive_turns(
    conn: sqlite3.Connection, ident: Ident, session: str, ids: list[int],
    *, summary_id: int | None = None, now: float | None = None,
) -> int:
    """把要淘汰的 turn 原文复制进 turn_archive（退役不删，borrow-plan P0-3）。

    为什么：摘要一旦漂移，删掉的原文就再也无法核对（v4 手册的 34 步真跑里，
    "写文档路径不带记忆"就是靠原文才能定位的）。归档表与 turn 同列 + archived_at，
    必须带三列身份条件（跨平台隔离红线）。
    """
    if not ids:
        return 0
    t = time.time() if now is None else now
    q = ",".join("?" * len(ids))
    w, args = ident.where()
    cur = conn.execute(
        "INSERT INTO turn_archive(platform,platform_user_id,scope,session,role,content,"
        "extra,ts,archived_at,summary_id)"
        f" SELECT platform,platform_user_id,scope,session,role,content,extra,ts,?,?"
        f" FROM turn WHERE id IN ({q}) AND {w} ORDER BY id ASC",
        (t, summary_id, *ids, *args),
    )
    return int(cur.rowcount or 0)


def evict_turns(
    conn: sqlite3.Connection, ident: Ident, session: str, keep: int,
    *, summary_id: int | None = None, now: float | None = None,
) -> tuple[int, list[str]]:
    """会话淘汰：只留最近 keep 条，返回 (删了多少条, 被删的用户发言)。

    摘要由调用方在删除前用这些用户发言生成，保证"删原文不丢信息"。
    v11 起先写 `turn_archive` 再删——退役不删，90 天后才物理清。
    """
    ids = session_turn_ids(conn, ident, session)
    keep = max(0, int(keep))
    if len(ids) <= keep:
        return 0, []
    drop = ids[: len(ids) - keep]
    q = ",".join("?" * len(drop))
    w, args = ident.where()
    rows = conn.execute(
        f"SELECT role, content FROM turn WHERE id IN ({q}) AND {w} ORDER BY id ASC", (*drop, *args)
    ).fetchall()
    archive_turns(conn, ident, session, drop, summary_id=summary_id, now=now)
    conn.execute(f"DELETE FROM turn WHERE id IN ({q}) AND {w}", (*drop, *args))
    return len(drop), [r["content"] for r in rows if r["role"] == "user"]


def archived_turns(
    conn: sqlite3.Connection, ident: Ident, session: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """回查被淘汰的原文（CLI `aiji turn-archive` / 排查摘要漂移用）。"""
    w, args = ident.where()
    sql = f"SELECT id,session,role,content,ts,archived_at,summary_id FROM turn_archive WHERE {w}"
    if session:
        sql += " AND session=?"
        args.append(session)
    # 按原对话时间倒序：归档行自增 id 取决于插入扫描顺序（靠不住），ts 才稳定。
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def prune_archive(conn: sqlite3.Connection, *, keep_days: float, now: float | None = None) -> int:
    """归档表滚动清理（默认 90 天）。只有这里会物理删原文。"""
    t = time.time() if now is None else now
    cur = conn.execute(
        "DELETE FROM turn_archive WHERE archived_at < ?", (t - float(keep_days) * 86400.0,)
    )
    return int(cur.rowcount or 0)


def profile_snapshot(conn: sqlite3.Connection, ident: Ident) -> dict[str, dict[str, Any]]:
    w, args = ident.where()
    return {
        r["key"]: {"value": r["value"], "confidence": r["confidence"], "updated_at": r["updated_at"]}
        for r in conn.execute(
            f"SELECT key,value,confidence,updated_at FROM profile WHERE {w}", tuple(args)
        ).fetchall()
    }


# ---- 媒体：只存引用 ----

def add_media(
    conn: sqlite3.Connection,
    ident: Ident,
    *,
    kind: str,
    ref: str,
    url: str | None = None,
    session: str | None = None,
    mime: str | None = None,
    size_bytes: int | None = None,
    expires_at: float | None = None,
    text: str | None = None,
    confidence: float | None = None,
    status: str = "pending",
    recognizer: str | None = None,
    elapsed_ms: float | None = None,
    extra: dict[str, Any] | None = None,
    now: float | None = None,
) -> int:
    """记一条媒体**引用** + 它的识别结果（降维后的文本、置信度、状态）。

    ref 是平台侧可再取的句柄；url 可能过期（看 expires_at）。
    `text/confidence/status/recognizer` 是识别层的落库结果，用于追溯"这条记忆从哪来、多可信"。
    """
    if not ref:
        raise ValueError("ref 不能为空：媒体只存可重新获取的引用")
    t = time.time() if now is None else now
    conn.execute(
        "INSERT INTO media(platform,platform_user_id,scope,session,kind,ref,url,mime,size_bytes,"
        "expires_at,text,confidence,status,recognizer,elapsed_ms,extra,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(platform,platform_user_id,scope,ref) DO UPDATE SET"
        " session=excluded.session, url=excluded.url, mime=excluded.mime,"
        " size_bytes=excluded.size_bytes,"
        " expires_at=excluded.expires_at, text=excluded.text,"
        " confidence=excluded.confidence, status=excluded.status,"
        " recognizer=excluded.recognizer, elapsed_ms=excluded.elapsed_ms,"
        " extra=excluded.extra, created_at=excluded.created_at",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"), session,
            kind, ref, url, mime, size_bytes, expires_at,
            text, confidence, status, recognizer, elapsed_ms,
            json.dumps(extra or {}, ensure_ascii=False), t,
        ),
    )
    row = conn.execute(
        "SELECT id FROM media WHERE platform=? AND platform_user_id=? AND scope=? AND ref=?",
        (ident.platform, ident.platform_user_id, str(ident.scope or "default"), ref),
    ).fetchone()
    return int(row["id"])


def media_for(
    conn: sqlite3.Connection, ident: Ident, session: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    w, args = ident.where()
    sql = (
        "SELECT id,kind,ref,url,mime,size_bytes,expires_at,text,confidence,status,"
        f"recognizer,elapsed_ms,session,extra FROM media WHERE {w}"
    )
    if session:
        sql += " AND session=?"
        args.append(session)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


# ---- 风格：只描述表达方式，不进人格核心 ----

def upsert_style(
    conn: sqlite3.Connection,
    ident: Ident,
    hints: Mapping[str, str],
    *,
    now: float | None = None,
) -> int:
    """风格维度累加：同值加证据数并抬置信度，异值按证据投票，不硬覆盖。"""
    t = time.time() if now is None else now
    n = 0
    for dim, value in (hints or {}).items():
        d = str(dim).strip()[:16]
        v = str(value).strip()[:32]
        if not d or not v:
            continue
        cur = conn.execute(
            "SELECT value, confidence, evidence FROM style"
            " WHERE platform=? AND platform_user_id=? AND scope=? AND dimension=?",
            (ident.platform, ident.platform_user_id, str(ident.scope or "default"), d),
        ).fetchone()
        if cur is None:
            conn.execute(
                "INSERT INTO style(platform,platform_user_id,scope,dimension,value,confidence,"
                "evidence,updated_at) VALUES(?,?,?,?,?,?,1,?)",
                (ident.platform, ident.platform_user_id, str(ident.scope or "default"), d, v, 0.55, t),
            )
        elif cur["value"] == v:
            conn.execute(
                "UPDATE style SET evidence=evidence+1,"
                " confidence=MIN(0.95, confidence + 0.05), updated_at=?"
                " WHERE platform=? AND platform_user_id=? AND scope=? AND dimension=?",
                (t, ident.platform, ident.platform_user_id, str(ident.scope or "default"), d),
            )
        else:
            # 换了表达方式：证据够多才翻转，避免一句反话改掉风格
            if cur["evidence"] <= 1:
                conn.execute(
                    "UPDATE style SET value=?, confidence=0.5, evidence=1, updated_at=?"
                    " WHERE platform=? AND platform_user_id=? AND scope=? AND dimension=?",
                    (v, t, ident.platform, ident.platform_user_id, str(ident.scope or "default"), d),
                )
            else:
                conn.execute(
                    "UPDATE style SET evidence=evidence-1, updated_at=?"
                    " WHERE platform=? AND platform_user_id=? AND scope=? AND dimension=?",
                    (t, ident.platform, ident.platform_user_id, str(ident.scope or "default"), d),
                )
        n += 1
    return n


def style_snapshot(conn: sqlite3.Connection, ident: Ident) -> dict[str, dict[str, Any]]:
    w, args = ident.where()
    return {
        r["dimension"]: {"value": r["value"], "confidence": r["confidence"], "evidence": r["evidence"]}
        for r in conn.execute(
            f"SELECT dimension,value,confidence,evidence FROM style WHERE {w}", tuple(args)
        ).fetchall()
    }


# ---- 自我层：涌现性格（物理底线在 aiji/selfhood.py，这里只存"长出来的"）----

TRAIT_MAX = 24           # 单个身份最多记 24 条性格（防无限膨胀，超出按置信度淘汰）
TRAIT_VALUE_CHARS = 60   # 单条倾向描述上限


def upsert_trait(
    conn: sqlite3.Connection,
    ident: Ident,
    name: str,
    value: str,
    *,
    origin: str = "reflect",
    weight: int = 1,
    now: float | None = None,
) -> str:
    """涌现性格的证据投票。

    返回 'new' / 'reinforce' / 'replaced' / 'weakened' / 'skip'。
    - 同名同值：证据 +weight，置信 +0.05×weight（封顶 0.95）
    - 同名异值：现有证据不足以支撑旧值（evidence ≤ weight）才翻转，否则扣证据
    """
    t = time.time() if now is None else now
    n = str(name).strip()[:16]
    v = str(value).strip()[:TRAIT_VALUE_CHARS]
    if not n or not v:
        return "skip"
    w1 = max(1, int(weight))
    scope = str(ident.scope or "default")
    cur = conn.execute(
        "SELECT value, confidence, evidence FROM trait"
        " WHERE platform=? AND platform_user_id=? AND scope=? AND name=?",
        (ident.platform, ident.platform_user_id, scope, n),
    ).fetchone()

    if cur is None:
        conn.execute(
            "INSERT INTO trait(platform,platform_user_id,scope,name,value,confidence,"
            "evidence,origin,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (ident.platform, ident.platform_user_id, scope, n, v, 0.5, w1, origin, t),
        )
        _trim_traits(conn, ident)
        return "new"
    if cur["value"] == v:
        conn.execute(
            "UPDATE trait SET evidence=evidence+?,"
            " confidence=MIN(0.95, confidence + 0.05*?), origin=?, updated_at=?"
            " WHERE platform=? AND platform_user_id=? AND scope=? AND name=?",
            (w1, w1, origin, t, ident.platform, ident.platform_user_id, scope, n),
        )
        return "reinforce"
    if int(cur["evidence"]) <= w1:
        conn.execute(
            "UPDATE trait SET value=?, confidence=0.5, evidence=?, origin=?, updated_at=?"
            " WHERE platform=? AND platform_user_id=? AND scope=? AND name=?",
            (v, w1, origin, t, ident.platform, ident.platform_user_id, scope, n),
        )
        return "replaced"
    conn.execute(
        "UPDATE trait SET evidence=evidence-?, confidence=MAX(0.2, confidence-0.1),"
        " updated_at=? WHERE platform=? AND platform_user_id=? AND scope=? AND name=?",
        (w1, t, ident.platform, ident.platform_user_id, scope, n),
    )
    return "weakened"


def _trim_traits(conn: sqlite3.Connection, ident: Ident) -> None:
    """超出 TRAIT_MAX 时淘汰证据最弱、置信最低的那条。"""
    scope = str(ident.scope or "default")
    rows = conn.execute(
        "SELECT name FROM trait WHERE platform=? AND platform_user_id=? AND scope=?"
        " ORDER BY evidence ASC, confidence ASC",
        (ident.platform, ident.platform_user_id, scope),
    ).fetchall()
    excess = len(rows) - TRAIT_MAX
    for r in rows[:max(0, excess)]:
        conn.execute(
            "DELETE FROM trait WHERE platform=? AND platform_user_id=? AND scope=? AND name=?",
            (ident.platform, ident.platform_user_id, scope, r["name"]),
        )


def trait_snapshot(
    conn: sqlite3.Connection, ident: Ident, *, limit: int = TRAIT_MAX
) -> list[dict[str, Any]]:
    """当前涌现出的性格，按置信度降序。"""
    w, args = ident.where()
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT name,value,confidence,evidence,origin,updated_at FROM trait"
            f" WHERE {w} ORDER BY confidence DESC, evidence DESC LIMIT ?",
            tuple(args) + (int(limit),),
        ).fetchall()
    ]


def global_traits(
    conn: sqlite3.Connection,
    *,
    min_identities: int = 2,
    limit: int = TRAIT_MAX,
) -> list[dict[str, Any]]:
    """Derive a stable persona baseline from habits repeated across identities."""
    rows = conn.execute(
        "SELECT name,value,"
        " COUNT(DISTINCT platform || char(31) || platform_user_id) AS identities,"
        " SUM(evidence) AS evidence,MAX(confidence) AS confidence,"
        " MAX(updated_at) AS updated_at"
        " FROM trait GROUP BY name,value"
        " HAVING COUNT(DISTINCT platform || char(31) || platform_user_id)>=?"
        " ORDER BY identities DESC,evidence DESC,confidence DESC LIMIT ?",
        (max(2, int(min_identities)), max(1, int(limit))),
    ).fetchall()
    return [
        {
            "name": row["name"],
            "value": row["value"],
            "identities": int(row["identities"]),
            "evidence": int(row["evidence"] or 0),
            "confidence": float(row["confidence"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
            "origin": "cross_identity",
        }
        for row in rows
    ]


# ---- 反思留痕（幂等游标：记住已反思到哪条 turn）----

def last_reflection(
    conn: sqlite3.Connection, ident: Ident, session: str
) -> dict[str, Any] | None:
    w, args = ident.where()
    r = conn.execute(
        f"SELECT up_to_turn,turns_seen,summary,deltas,created_at FROM reflection"
        f" WHERE {w} AND session=? ORDER BY id DESC LIMIT 1",
        tuple(args) + (session,),
    ).fetchone()
    return dict(r) if r else None


def log_reflection(
    conn: sqlite3.Connection,
    ident: Ident,
    session: str,
    up_to_turn: int,
    turns_seen: int,
    summary: str,
    deltas: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> int:
    t = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO reflection(platform,platform_user_id,scope,session,up_to_turn,"
        "turns_seen,summary,deltas,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"),
            session, int(up_to_turn), int(turns_seen), str(summary or "")[:1000],
            json.dumps(deltas or [], ensure_ascii=False), t,
        ),
    )
    return int(cur.lastrowid)


# ---- 摘要 ----

def put_summary(
    conn: sqlite3.Connection,
    ident: Ident,
    session: str,
    upto_turn: int,
    text: str,
    *,
    extra: dict[str, Any] | None = None,
    source_hash: str = "",
    source_turn_ids: str = "",
    now: float | None = None,
) -> int:
    t = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO summary(platform,platform_user_id,scope,session,upto_turn,text,extra,ts,"
        "source_hash,source_turn_ids) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"),
            session, upto_turn, text[:1000],
            json.dumps(extra or {}, ensure_ascii=False), t,
            source_hash or "", source_turn_ids or "",
        ),
    )
    return int(cur.lastrowid)


def summary_by_hash(
    conn: sqlite3.Connection, ident: Ident, session: str, source_hash: str
) -> dict[str, Any] | None:
    """同会话、同原文哈希的旧摘要（命中就复用，不调模型——memory-brain 的做法）。

    语义：这一段原文已经被总结过，且原文没变（哈希相同）→ 直接沿用旧摘要、
    只把 upto_turn 游标推到新位置。
    """
    if not source_hash:
        return None
    w, args = ident.where()
    row = conn.execute(
        f"SELECT id,session,upto_turn,text,ts FROM summary WHERE {w}"
        " AND session=? AND source_hash=? ORDER BY id DESC LIMIT 1",
        (*args, session, source_hash),
    ).fetchone()
    return dict(row) if row else None


def latest_summary(
    conn: sqlite3.Connection, ident: Ident, session: str | None = None
) -> dict[str, Any] | None:
    w, args = ident.where()
    sql = f"SELECT session,upto_turn,text,ts FROM summary WHERE {w}"
    if session:
        sql += " AND session=?"
        args.append(session)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", tuple(args)).fetchone()
    return dict(row) if row else None


# ---- 代谢层：可提取度 / 强化 / 解冻 / 遗忘 / 自我记忆（v11，borrow-plan P0+P2）----

def retrievability(f: Fact, now: float | None = None) -> float:
    """FSRS-5 可提取度：R=(1+F·t/S)^-0.5，t=距上次想起的天数（没想起过就用 updated_at）。

    与 recall.score_fact 的"排序衰减"是**两个用途**：那个决定"这条排多前"，
    这个决定"这条还该不该自动冒出来"（门禁），两者并存、不合并成一个数。
    """
    t = time.time() if now is None else now
    last = f.last_used_at or f.updated_at or f.created_at
    days = max(0.0, (t - float(last)) / 86400.0)
    s = max(0.5, float(f.stability or params.STABILITY_INIT_DAYS))
    return float((1.0 + params.FSRS_F * days / s) ** -0.5)


def should_freeze(f: Fact, now: float | None = None) -> bool:
    """该不该冻结：低置信 + 从未被想起 + 长期没更新（对齐 janitor 的旧 stale 判据）。"""
    t = time.time() if now is None else now
    if f.status != "active" or f.frozen:
        return False
    if float(f.confidence) >= params.STALE_CONFIDENCE:
        return False
    if int(f.hit_count or 0) > 0 or f.last_used_at is not None:
        return False
    if (t - float(f.updated_at)) < params.STALE_DAYS * 86400.0:
        return False
    return retrievability(f, t) < params.FREEZE_BELOW


def bump_usage(
    conn: sqlite3.Connection, ident: Ident, ids: list[int], *, now: float | None = None
) -> int:
    """读→强化：注入命中后给稳定性加分（borrow-plan P0-1 + P0-2 的输入）。

    - 跨天再想起 +FSRS_SPACED_GAIN，同一天重复只 +FSRS_SAME_DAY_GAIN（间隔重复）；
    - REVIEW_COOLDOWN_S 内重复注入只算一次（防一轮里反复命中刷分）；
    - **不动 updated_at**（那是"内容变更时间"，衰减语义要用它）；
    - 冻结中的事实被读到 → 顺手解冻（"查到即解冻"）。
    """
    t = time.time() if now is None else now
    w, args = ident.where()
    n = 0
    for fid in ids or []:
        row = conn.execute(
            f"SELECT last_used_at, stability, frozen FROM fact WHERE {w} AND id=?",
            (*args, int(fid)),
        ).fetchone()
        if row is None:
            continue
        last = row["last_used_at"]
        if last is not None and (t - float(last)) < params.REVIEW_COOLDOWN_S:
            continue
        s = float(row["stability"] or params.STABILITY_INIT_DAYS)
        spaced = last is None or (t - float(last)) >= 86400.0
        gain = params.FSRS_SPACED_GAIN if spaced else params.FSRS_SAME_DAY_GAIN
        s2 = min(params.STABILITY_MAX_DAYS, s * (1.0 + gain))
        conn.execute(
            f"UPDATE fact SET hit_count=hit_count+1, last_used_at=?, stability=?,"
            f" frozen=0 WHERE {w} AND id=?",
            (t, s2, *args, int(fid)),
        )
        n += 1
    return n


def freeze_facts(conn: sqlite3.Connection, *, now: float | None = None) -> int:
    """把该冻结的低置信陈事实冻结（janitor 调用；不再软删——退役不删）。"""
    t = time.time() if now is None else now
    rows = conn.execute(
        "SELECT * FROM fact WHERE status='active' AND frozen=0 AND confidence<?"
        " AND hit_count=0 AND last_used_at IS NULL AND updated_at<?",
        (params.STALE_CONFIDENCE, t - params.STALE_DAYS * 86400.0),
    ).fetchall()
    n = 0
    for r in rows:
        f = _fact(r)
        if not should_freeze(f, t):
            continue
        conn.execute("UPDATE fact SET frozen=1 WHERE id=?", (f.id,))
        n += 1
    return n


def thaw_fact(
    conn: sqlite3.Connection, ident: Ident, fact_id: int, *, now: float | None = None
) -> bool:
    """显式想起一条冻结的事实：解冻并加分（"查到即解冻"）。"""
    t = time.time() if now is None else now
    w, args = ident.where()
    cur = conn.execute(
        f"UPDATE fact SET frozen=0, stability=MIN(?, stability*1.2), hit_count=hit_count+1,"
        f" last_used_at=? WHERE {w} AND id=? AND frozen=1",
        (params.STABILITY_MAX_DAYS, t, *args, int(fact_id)),
    )
    return bool(cur.rowcount)


def forget_facts(
    conn: sqlite3.Connection, ident: Ident, *, fact_id: int | None = None,
    like: str = "", now: float | None = None,
) -> int:
    """显式遗忘（手册 v5"你让她忘掉，就真删"，我们改成"立刻不可见 + 留审计"）。

    为什么不是物理删：fact_log 与 fact_link 会悬空，且"被删了什么"以后无法复核。
    语义上等价——注入/召回/画像一律看不到它（status='deleted' 是全链路的过滤条件）。
    """
    t = time.time() if now is None else now
    w, args = ident.where()
    if fact_id is not None:
        rows = conn.execute(
            f"SELECT id FROM fact WHERE {w} AND id=? AND status='active'", (*args, int(fact_id))
        ).fetchall()
    elif like:
        rows = conn.execute(
            f"SELECT id FROM fact WHERE {w} AND status='active' AND text LIKE ?",
            (*args, f"%{like}%"),
        ).fetchall()
    else:
        return 0
    ids = [int(r["id"]) for r in rows]
    for fid in ids:
        conn.execute(
            f"UPDATE fact SET status='deleted', updated_at=? WHERE {w} AND id=?",
            (t, *args, fid),
        )
        log_fact(conn, ident, "forget", fid, detail={"via": "explicit"}, now=t)
    return len(ids)


# ---- 自我记忆（手册 v5 第五层：她自己的事，与 trait 分开）----

def add_self_memory(
    conn: sqlite3.Connection, text: str, *, kind: str = "pref", scope: str = "default",
    importance: float = 0.5, now: float | None = None,
) -> int:
    t = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO self_memory(platform,platform_user_id,scope,kind,text,importance,created_at)"
        " VALUES('*','*',?,?,?,?,?)",
        (scope or "default", kind, text[:200], float(importance), t),
    )
    return int(cur.lastrowid)


def self_memory_list(
    conn: sqlite3.Connection, *, scope: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM self_memory WHERE status='active'"
    args: list[Any] = []
    if scope:
        sql += " AND scope=?"
        args.append(scope)
    sql += " ORDER BY COALESCE(last_used_at, created_at) DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def trim_self_memory(conn: sqlite3.Connection, cap: int | None = None) -> int:
    """超上限淘汰"最久没用"的（手册 v5 的做法：200 条上限附近，淘汰最久没用到的）。"""
    limit = params.SELF_MEMORY_MAX if cap is None else int(cap)
    cur = conn.execute(
        "UPDATE self_memory SET status='archived' WHERE status='active' AND id NOT IN ("
        " SELECT id FROM self_memory WHERE status='active'"
        " ORDER BY COALESCE(last_used_at, created_at) DESC, id DESC LIMIT ?)",
        (limit,),
    )
    return int(cur.rowcount or 0)


# ---- 注入留痕 + 反馈 ----

def log_injection(
    conn: sqlite3.Connection,
    ident: Ident,
    query: str,
    item_ids: Sequence[int],
    prompt: str,
    *,
    session: str | None = None,
    now: float | None = None,
) -> int:
    t = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO injection(platform,platform_user_id,scope,session,query,item_ids,prompt,"
        "created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"), session,
            query[:500], json.dumps(list(item_ids)), prompt[:4000], t,
        ),
    )
    return int(cur.lastrowid)


def apply_feedback(
    conn: sqlite3.Connection,
    ident: Ident,
    injection_id: int | None,
    used_ids: Sequence[int],
    *,
    helpful: bool = True,
    note: str = "",
) -> int:
    """主模型实际引用了哪些记忆：命中计数 +1，注入记录写上反馈。

    helpful=False（纠错/负反馈）：对命中的 fact 做**负向下调**——
    confidence × NEG_FACTOR、地板 FEEDBACK_FLOOR（方案 1.11 #4），不单次覆盖。

    helpful=True（正向确认）：命中计数 +1，**且对曾被下调的 fact 回升**——
    confidence × POS_FACTOR、封顶 POS_CAP（不对称升降，方案 1.7 复盘补）：
    只升"低于其来源基线"的行，防止一次夸就把先验满血；
    未被下调过的行（confidence ≥ 0.85）不动置信，只计数。
    """
    t = time.time()
    w, wargs = ident.where()
    n = 0
    for fid in used_ids:
        if not helpful:
            # 负向下调：地板 FEEDBACK_FLOOR，防穿透（janitor 也不会物理删，软删可回滚）
            cur = conn.execute(
                f"UPDATE fact SET confidence=MAX(?, confidence*?), updated_at=?"
                f" WHERE {w} AND id=? AND status='active'",
                (params.FEEDBACK_FLOOR, params.NEG_FACTOR, t, *wargs, int(fid)),
            )
            if cur.rowcount:
                log_fact(conn, ident, "downgrade", int(fid), None,
                         {"source": "feedback_negative", "factor": params.NEG_FACTOR}, now=t)
            n += cur.rowcount
            continue
        # 正向：查 fact_log 是否有下调记录（confidence 是"被下调过"还是"天生低"——
        # 2026-10-29 复盘：写入即 0.7 的行被判 <0.85 误升到 0.875，语义错了）。
        downgraded = conn.execute(
            "SELECT 1 FROM fact_log WHERE fact_id=? AND op IN ('downgrade','feedback_negative')"
            " LIMIT 1", (int(fid),),
        ).fetchone()
        if downgraded:
            cur = conn.execute(
                f"UPDATE fact SET confidence=MIN(?, confidence*?), hit_count=hit_count+1,"
                f" last_used_at=? WHERE {w} AND id=? AND status='active'",
                (params.POS_CAP, params.POS_FACTOR, t, *wargs, int(fid)),
            )
        else:
            cur = conn.execute(
                f"UPDATE fact SET hit_count=hit_count+1, last_used_at=? WHERE {w} AND id=?",
                (t, *wargs, int(fid)),
            )
        n += cur.rowcount
    if injection_id:
        iw, iwargs = ident.where()
        conn.execute(
            f"UPDATE injection SET feedback=? WHERE id=? AND {iw}",
            (json.dumps({"helpful": helpful, "used": list(used_ids), "note": note[:200]},
                        ensure_ascii=False), int(injection_id),
             *iwargs),
        )
    return n


def injection_stats(conn: sqlite3.Connection, ident: Ident | None = None) -> dict[str, Any]:
    one = lambda sql, args=(): conn.execute(sql, args).fetchone()[0]  # noqa: E731
    with_fb = one("SELECT COUNT(*) FROM injection WHERE feedback IS NOT NULL")
    total = one("SELECT COUNT(*) FROM injection")
    used = one("SELECT COALESCE(SUM(hit_count),0) FROM fact")
    return {"injections": total, "with_feedback": with_fb, "fact_hits": used}


# ---- 纠错溯源（方案 dafeiyu-plan 1.7 + 1.7 轮1补）----

def last_injection_with_feedback(
    conn: sqlite3.Connection, ident: Ident, session: str | None = None,
) -> tuple[int | None, list[int]]:
    """该身份最近一次注入及其 cited ids（调用方回报的 used）。

    口径：cited = injection.feedback JSON 的 "used" 列表（生成方回报实际引用的 fact）；
    无 feedback 时退化为 item_ids（注入候选，非 cited——幅度减半由调用方处理）。
    """
    w, wargs = ident.where()
    sql = (f"SELECT id, item_ids, feedback FROM injection WHERE {w}")
    if session:
        sql += " AND session=?"
        wargs = (*wargs, session)
    sql += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(sql, wargs).fetchone()
    if not row:
        return None, []
    item_ids = json.loads(row["item_ids"] or "[]")
    if row["feedback"]:
        # feedback 存在即以回报为准——used=[] 是"生成方明确没引用"，
        # 不能退化到 item_ids（那会把未引用候选当 cited，误伤面扩大）。
        try:
            fb = json.loads(row["feedback"])
            cited = [int(x) for x in fb.get("used") or []]
        except (ValueError, TypeError):
            cited = []
        return int(row["id"]), cited
    # 无 feedback：退化为候选 item_ids（调用方按幅度减半处理）
    return int(row["id"]), item_ids


def apply_correction(
    conn: sqlite3.Connection,
    ident: Ident,
    *,
    cited_ids: Sequence[int],
    predicate_hint: str = "",
    text: str = "",
    floor: float = params.FEEDBACK_FLOOR,
    now: float | None = None,
) -> dict[str, Any]:
    """纠错下修：**只打 cited**（作用域正确性），无 cited 才按谓词/文本降级定位且幅度减半。

    - 有 cited（生成方回报）：confidence × params.NEG_FACTOR，地板 floor
    - 无 cited：按 predicate_hint（或文本 2-gram 重叠 ≥0.3）定位同身份 active fact，
      × params.NEG_FACTOR_FALLBACK（减半），地板 floor
    返回 {"cited": [...], "fallback": [...], "before_after": [(id, old, new)]} 供审计断言。
    """
    t = time.time() if now is None else now
    w, wargs = ident.where()
    result: dict[str, Any] = {"cited": [], "fallback": [], "before_after": []}

    def _downgrade(fid: int, factor: float, bucket: str) -> None:
        row = conn.execute(
            f"SELECT confidence FROM fact WHERE {w} AND id=? AND status='active'",
            (*wargs, int(fid)),
        ).fetchone()
        if not row:
            return
        old = float(row["confidence"])
        new = max(floor, old * factor)
        conn.execute(
            f"UPDATE fact SET confidence=?, updated_at=? WHERE {w} AND id=?",
            (new, t, *wargs, int(fid)),
        )
        log_fact(conn, ident, "downgrade", int(fid), None,
                 {"source": f"correction_{bucket}", "factor": factor,
                  "old": round(old, 4), "new": round(new, 4)}, now=t)
        result[bucket].append(int(fid))
        result["before_after"].append((int(fid), round(old, 4), round(new, 4)))

    # 路径 1：cited 精确作用域
    for fid in cited_ids:
        _downgrade(fid, params.NEG_FACTOR, "cited")
    if result["cited"]:
        return result

    # 路径 2：降级定位（幅度减半 ×0.3）
    if predicate_hint:
        rows = conn.execute(
            f"SELECT id, text FROM fact WHERE {w} AND status='active' AND predicate=?",
            (*wargs, predicate_hint),
        ).fetchall()
    else:
        rows = []
    if not rows and text:
        rows = conn.execute(
            f"SELECT id, text FROM fact WHERE {w} AND status='active'",
            wargs,
        ).fetchall()
        from .recall import _overlap  # 局部导入避免环
        rows = [r for r in rows if _overlap(text, r["text"]) >= 0.3]
    for r in rows[:5]:
        _downgrade(int(r["id"]), params.NEG_FACTOR_FALLBACK, "fallback")
    return result


# ---- 检索 ----

def _ngrams(q: str, n: int = 3) -> list[str]:
    seen: list[str] = []
    for i in range(len(q) - n + 1):
        g = q[i : i + n]
        if g not in seen:
            seen.append(g)
    return seen


def facts_as_of(
    conn: sqlite3.Connection, ident: Ident, as_of: float,
    *,
    predicate: str | None = None, limit: int = 50,
) -> list[Fact]:
    """bi-temporal 回放（Graphiti validity window 语义，S4 archive_replay 前置）：

    返回"在 as_of 时刻为真"的该身份全部事实——当时视角，不是现状。
    判据（与写路径严格对称）：
      - valid_from 回填 created_at（v4 迁移），旧行有值；
      - active 行 = 现在仍真（valid_to IS NULL）；
      - superseded/expired 行 = valid_to 时刻之前为真。
    即：valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of)。
    跨身份过滤是硬约束（串号事故面）。
    """
    w, wargs = ident.where("f")
    sql = (
        f"SELECT f.* FROM fact f WHERE {w}"
        " AND f.valid_from<=? AND (f.valid_to IS NULL OR f.valid_to>?)"
    )
    args: list[Any] = [*wargs, float(as_of), float(as_of)]
    if predicate:
        sql += " AND f.predicate=?"
        args.append(predicate)
    sql += " ORDER BY f.valid_from DESC LIMIT ?"
    args.append(int(limit))
    return [_fact(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def search_facts(
    conn: sqlite3.Connection, ident: Ident, query: str, limit: int = 24,
    *, include_frozen: bool = False,
) -> list[Fact]:
    """FTS5(trigram) → 3-gram OR → 2-gram LIKE → 实义单字，四级降级；全程按身份过滤。

    include_frozen=False（默认）时排除 frozen=1：冻结的记忆不进召回/注入，
    但显式查询（CLI/管理接口传 True）仍能查到——"遗忘是可访问性，不是删除"。
    """
    q = (query or "").strip()
    if not q:
        return []
    w, wargs = ident.where("f")
    live = "f.status='active'" + ("" if include_frozen else " AND f.frozen=0")
    phrase = '"' + q.replace('"', '""') + '"'
    rows: list[sqlite3.Row] = []

    def fts(expr: str) -> list[sqlite3.Row]:
        try:
            return conn.execute(
                "SELECT f.* FROM fact_fts JOIN fact f ON f.id = fact_fts.rowid"
                f" WHERE fact_fts MATCH ? AND {live} AND {w}"
                " ORDER BY bm25(fact_fts) LIMIT ?",
                (expr, *wargs, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    rows = fts(phrase)
    if not rows:
        grams = _ngrams(norm(q), 3)
        if grams:
            rows = fts(" OR ".join('"' + g.replace('"', '""') + '"' for g in grams))
    if not rows:
        grams = _ngrams(norm(q), 2)[:8]
        if grams:
            terms = [f"%{g}%" for g in grams]
            cond = " OR ".join(["(text LIKE ? OR object LIKE ? OR subject LIKE ?)"] * len(terms))
            args: list[Any] = []
            for t in terms:
                args.extend([t, t, t])
            rows = conn.execute(
                f"SELECT * FROM fact f WHERE {live} AND {w} AND ({cond})"
                " ORDER BY updated_at DESC LIMIT ?",
                (*wargs, *args, limit),
            ).fetchall()
    if not rows:
        chars: list[str] = []
        for ch in norm(q):
            if ch not in _STOP and "\u4e00" <= ch <= "\u9fff" and ch not in chars:
                chars.append(ch)
        chars = chars[:4]
        if chars:
            cond = " OR ".join(["text LIKE ?"] * len(chars))
            rows = conn.execute(
                f"SELECT * FROM fact f WHERE {live} AND {w} AND ({cond})"
                " ORDER BY updated_at DESC LIMIT ?",
                (*wargs, *[f"%{c}%" for c in chars], limit),
            ).fetchall()
    return [_fact(r) for r in rows]


# ---- 队列 ----

@dataclass(slots=True)
class QueueItem:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int = 0


def enqueue(
    conn: sqlite3.Connection,
    ident: Ident,
    kind: str,
    payload: dict[str, Any],
    *,
    now: float | None = None,
) -> int:
    t = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO write_queue(platform,platform_user_id,scope,kind,payload,state,created_at,next_at)"
        " VALUES(?,?,?,?,?,'pending',?,?)",
        (
            ident.platform, ident.platform_user_id, str(ident.scope or "default"),
            kind, json.dumps(payload, ensure_ascii=False), t, t,
        ),
    )
    return int(cur.lastrowid)


def claim_pending(conn: sqlite3.Connection, limit: int = 8, *, now: float | None = None) -> list[QueueItem]:
    t = time.time() if now is None else now
    rows = conn.execute(
        "SELECT id,kind,payload,attempts FROM write_queue WHERE state='pending' AND next_at<=?"
        " ORDER BY id LIMIT ?",
        (t, limit),
    ).fetchall()
    return [QueueItem(r["id"], r["kind"], json.loads(r["payload"]), r["attempts"]) for r in rows]


def fail(conn: sqlite3.Connection, item_id: int, err: str, *, retry_at: float | None = None) -> None:
    if retry_at is None:
        conn.execute(
            "UPDATE write_queue SET state='failed', attempts=attempts+1, last_error=? WHERE id=?",
            (err[:500], item_id),
        )
    else:
        conn.execute(
            "UPDATE write_queue SET attempts=attempts+1, last_error=?, next_at=? WHERE id=?",
            (err[:500], retry_at, item_id),
        )


def done(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("UPDATE write_queue SET state='done' WHERE id=?", (item_id,))


def stats(conn: sqlite3.Connection, ident: Ident | None = None) -> dict[str, Any]:
    one = lambda sql, args=(): conn.execute(sql, args).fetchone()[0]  # noqa: E731
    if ident is None:
        base = {}
    else:
        w, args = ident.where()
        base = {f"facts_active_{ident.platform}": one(
            f"SELECT COUNT(*) FROM fact WHERE {w} AND status='active'", tuple(args))}
    return {
        "facts": one("SELECT COUNT(*) FROM fact WHERE status='active'"),
        "facts_all": one("SELECT COUNT(*) FROM fact"),
        "expired": one("SELECT COUNT(*) FROM fact WHERE status='expired'"),
        "links": one("SELECT COUNT(*) FROM fact_link"),
        "fact_ops": one("SELECT COUNT(*) FROM fact_log"),
        "knowledge": one("SELECT COUNT(*) FROM fact WHERE status='active' AND kind='knowledge'"),
        "relations": one("SELECT COUNT(*) FROM fact WHERE status='active' AND kind='relation'"),
        "profile": one("SELECT COUNT(*) FROM profile"),
        "style": one("SELECT COUNT(*) FROM style"),
        "turns": one("SELECT COUNT(*) FROM turn"),
        "summaries": one("SELECT COUNT(*) FROM summary"),
        "media": one("SELECT COUNT(*) FROM media"),
        "media_unrecognized": one(
            "SELECT COUNT(*) FROM media WHERE status IN ('unsupported','error','timeout')"),
        "accounts": one("SELECT COUNT(*) FROM account"),
        "platforms": [r[0] for r in conn.execute(
            "SELECT DISTINCT platform FROM fact UNION SELECT platform FROM account").fetchall()],
        "queue_pending": one("SELECT COUNT(*) FROM write_queue WHERE state='pending'"),
        "queue_failed": one("SELECT COUNT(*) FROM write_queue WHERE state='failed'"),
        **base,
    }


def apply_extraction(
    conn: sqlite3.Connection,
    ident: Ident,
    facts: Iterable[FactDraft],
    profile: dict[str, str] | None = None,
    *,
    style: Mapping[str, str] | None = None,
    summary: tuple[str, int] | None = None,
    session: str = "",
    source: str = "",
    source_kind: str = "model",
) -> dict[str, Any]:
    """一次事务写入抽取结果。返回 {"fact_ids":[...], "superseded":[...], "profile":n, ...}。"""
    out: dict[str, Any] = {"fact_ids": [], "superseded": [], "profile": 0, "style": 0, "summary": 0}
    for d in facts:
        fid, old = add_fact(conn, ident, d, source=source, source_kind=source_kind)
        if fid:  # 否定句只让旧行失效，没有新行
            out["fact_ids"].append(fid)
        out["superseded"].extend(old)
    for k, v in (profile or {}).items():
        upsert_profile(conn, ident, k, v, confidence=0.85)
        out["profile"] += 1
    if style:
        out["style"] = upsert_style(conn, ident, style)
    if summary and summary[0]:
        text, upto = summary
        put_summary(conn, ident, session, upto, text, extra={"source_kind": source_kind})
        out["summary"] = 1
    return out


__all__ = [
    "Fact", "FactDraft", "Ident", "QueueItem",
    "add_fact", "add_media", "add_turn", "apply_extraction", "apply_feedback",
    "base_importance", "claim_pending", "done", "ensure_account", "enqueue",
    "evict_turns", "facts_as_of", "fail", "global_traits", "injection_stats",
    "last_reflection",
    "latest_summary", "link_facts", "links_for", "log_fact", "log_injection",
    "log_reflection", "media_for", "norm", "plan_reconciliation",
    "profile_snapshot", "put_summary", "recent_scope_turns", "recent_turns", "search_facts",
    "session_turn_ids", "stats", "style_snapshot", "trait_snapshot", "turn_count",
    "turn_by_external_id", "upsert_profile", "upsert_style", "upsert_trait",
]
