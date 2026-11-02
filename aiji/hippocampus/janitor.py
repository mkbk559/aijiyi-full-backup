"""清理 Worker（方案 dafeiyu-plan 1.8）：独立低频 + 水位触发。

职责：过期清理、低置信软删除、去重合并、fact_link 边数封顶（每事实 ≤3）、
heart_state day_bucket 过期清理、event_log 滚动保留。
铁律：软删除不物理删（status 改写，行保留）；写全部走 db.tx() 单写者；
降层归档暂缓（L1/L2 分层未做，见方案 1.8 注）；与反思并发冲突靠行级
乐观锁（fact.version，UPDATE ... WHERE version=?）。

单实例（1.2 轮2补）：经 heart_state 租约（process_id+lease_until）抢锁，
抢不到跳过本轮——多进程部署时不会双跑。
"""

from __future__ import annotations

import json
import time
import uuid

from aiji.right_hand import params
from aiji.hippocampus import store
from aiji.hippocampus.db import DB

LEASE_KEY = "janitor_lease"
JANITOR_ID = "janitor"

# 阈值全部来自 params.py（阶段 0 固化：数值+来源标注只在那边维护）
CONF_FLOOR = params.CONF_FLOOR                    # 0.15 纠错地板（1.11 #4）
STALE_DAYS = params.STALE_DAYS
STALE_CONFIDENCE = params.STALE_CONFIDENCE
LINK_CAP = params.LINK_CAP                        # 3 每事实关联边上限
EVENTLOG_KEEP_DAYS = params.EVENTLOG_KEEP_DAYS    # 7 天
HEARTSTATE_KEEP_BUCKETS = params.HEARTSTATE_KEEP_BUCKETS  # 2 天
LEASE_TTL = params.JANITOR_LEASE_TTL              # 600s 单实例租约
TURN_ARCHIVE_KEEP_DAYS = params.TURN_ARCHIVE_KEEP_DAYS  # 90 天归档保留（退役不删）


def _day_bucket(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class Janitor:
    def __init__(self, db: DB, *, purge: "callable | None" = None):
        """purge(ids)：软删后从向量索引清除这些 id（防止语义命中回流已删内容）。"""
        self.db = db
        self.purge = purge
        self.instance_id = f"janitor-{uuid.uuid4().hex[:12]}"
        self.stats = {"runs": 0, "skipped_lease": 0, "expired": 0, "floor_deleted": 0,
                      "stale_frozen": 0, "dedup_merged": 0, "links_capped": 0,
                      "state_buckets": 0, "eventlog": 0, "purged_index": 0,
                      "archive_pruned": 0, "self_memory_trimmed": 0}

    # ---- 单实例租约（1.2 轮2补） ----

    def _acquire_lease(self, *, now: float, ttl: float = LEASE_TTL) -> bool:
        payload = json.dumps(
            {"process": self.instance_id, "lease_until": now + ttl},
            ensure_ascii=False,
        )
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO heart_state(platform,platform_user_id,scope,state_key,day_bucket,"
                "value,updated_at) VALUES('_sys','_sys','default',?,'0',?,?)"
                " ON CONFLICT(platform,platform_user_id,scope,state_key,day_bucket)"
                " DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
                " WHERE COALESCE(CAST(json_extract(heart_state.value, '$.lease_until')"
                " AS REAL) <= ?, 1)",
                (LEASE_KEY, payload, now, now),
            )
            return bool(cur.rowcount)

    # ---- 主入口 ----

    def run(self, *, now: float | None = None, water_level: int | None = None) -> dict:
        t = time.time() if now is None else now
        if not self._acquire_lease(now=t):
            self.stats["skipped_lease"] += 1
            return {"skipped": True}
        self.stats["runs"] += 1
        try:
            self._expire_old(t)
            self._floor_delete(t)
            self._stale_freeze(t)
            self._dedup_merge(t)
            self._cap_links()
            self._prune_state(t)
            self._prune_eventlog(t)
            self._prune_archive(t)
            self._trim_self_memory()
            self._purge_index()
        finally:
            # 正常完成立即释放租约；崩溃则等 TTL（600s）自然过期——
            # 失败模式明确：持锁进程崩溃 → 最长停摆 TTL 秒，无死锁面。
            self._release_lease(now=t)
        return {"skipped": False, **{k: v for k, v in self.stats.items() if v}}

    def _release_lease(self, *, now: float) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE heart_state SET value=?, updated_at=? WHERE platform='_sys'"
                " AND platform_user_id='_sys' AND scope='default' AND state_key=?"
                " AND day_bucket='0' AND json_extract(value, '$.process')=?",
                (
                    json.dumps({"process": self.instance_id, "lease_until": 0.0}),
                    now,
                    LEASE_KEY,
                    self.instance_id,
                ),
            )

    def _purge_index(self) -> None:
        """软删行从向量索引清除（防语义命中回流）。"""
        if self.purge is None:
            return
        rows = self.db.read(
            "SELECT id FROM fact WHERE status IN ('deleted','expired','superseded')")
        ids = [int(r["id"]) for r in rows]
        if ids:
            try:
                self.purge(ids)
                self.stats["purged_index"] += len(ids)
            except Exception:  # noqa: BLE001 - 索引清除失败不影响主流程
                pass

    # ---- 过期：valid_to 已过且还是 active 的（防御性，正常路径写入时就改） ----

    def _expire_old(self, t: float) -> None:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE fact SET status='expired', updated_at=? "
                " WHERE status='active' AND valid_to IS NOT NULL AND valid_to<?", (t, t))
            self.stats["expired"] += cur.rowcount

    # ---- 地板：低置信软删（不物理删，可回滚） ----

    def _floor_delete(self, t: float) -> None:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE fact SET status='deleted', updated_at=? "
                " WHERE status='active' AND confidence<?", (t, CONF_FLOOR))
            self.stats["floor_deleted"] += cur.rowcount

    def _stale_freeze(self, t: float) -> None:
        """低置信陈事实 → **冻结**（v11 起不再软删，borrow-plan P0-2）。

        为什么改：软删是不可逆的"看不见"，冻结是"不主动冒出来、但查得到"。
        两者对 prompt 的效果相同（都不注入），但冻结保留了回滚与事后核对的能力。
        """
        with self.db.tx() as c:
            n = store.freeze_facts(c, now=t)
        self.stats["stale_frozen"] += n

    def _prune_archive(self, t: float) -> None:
        """turn 归档表滚动清理（默认 90 天）：只有这里会物理删原文。"""
        with self.db.tx() as c:
            n = store.prune_archive(c, keep_days=TURN_ARCHIVE_KEEP_DAYS, now=t)
        self.stats["archive_pruned"] += n

    def _trim_self_memory(self) -> None:
        """自我记忆超上限淘汰最久没用（手册 v5：200 条上限附近）。"""
        with self.db.tx() as c:
            n = store.trim_self_memory(c)
        self.stats["self_memory_trimmed"] += n

    # ---- 去重合并：同身份同 subject+predicate+object 且都 active，留 mentions 最高的 ----

    def _dedup_merge(self, t: float) -> None:
        """同身份同 (subject, predicate, object) 多条 active → 保留最早一条，
        其余软删（deleted，可回滚）；保留行 mentions 吸收被删行的提及数。"""
        rows = self.db.read(
            "SELECT id, platform, platform_user_id, scope, subject, predicate, object,"
            " mentions, version FROM fact WHERE status='active'"
            " AND id NOT IN (SELECT MIN(id) FROM fact WHERE status='active'"
            "  GROUP BY platform, platform_user_id, scope, subject, predicate, object)"
        )
        if not rows:
            return
        with self.db.tx() as c:
            for r in rows:
                # 乐观锁软删（version 不匹配就跳过——反思 Worker 正在改它）
                cur = c.execute(
                    "UPDATE fact SET status='deleted', updated_at=? WHERE id=? AND version=?"
                    " AND status='active'",
                    (t, r["id"], r["version"]))
                if cur.rowcount:
                    # 保留行吸收 mentions（去重合并语义：重复即加强）
                    c.execute(
                        "UPDATE fact SET mentions=mentions+? WHERE id=("
                        " SELECT MIN(id) FROM fact WHERE status='active' AND platform=?"
                        " AND platform_user_id=? AND scope=? AND subject=? AND predicate=?"
                        " AND object=?)",
                        (r["mentions"], r["platform"], r["platform_user_id"], r["scope"],
                         r["subject"], r["predicate"], r["object"]))
                    self.stats["dedup_merged"] += 1

    # ---- fact_link 边数封顶（每事实 ≤ LINK_CAP） ----

    def _cap_links(self) -> None:
        rows = self.db.read(
            "SELECT src_id, COUNT(*) n FROM fact_link GROUP BY src_id HAVING n>?", (LINK_CAP,))
        for r in rows:
            with self.db.tx() as c:
                c.execute(
                    "DELETE FROM fact_link WHERE rowid IN ("
                    " SELECT rowid FROM fact_link WHERE src_id=?"
                    " ORDER BY weight DESC, id DESC LIMIT -1 OFFSET ?)",
                    (r["src_id"], LINK_CAP))
                self.stats["links_capped"] += 1

    # ---- heart_state day_bucket 过期（1.11 #5：保留 2 天） ----

    def _prune_state(self, t: float) -> None:
        cutoff = _day_bucket(t - HEARTSTATE_KEEP_BUCKETS * 86400)
        with self.db.tx() as c:
            cur = c.execute(
                "DELETE FROM heart_state WHERE day_bucket!='0' AND day_bucket<?", (cutoff,))
            self.stats["state_buckets"] += cur.rowcount

    # ---- event_log 滚动保留（1.11 #5：7 天；短期缓冲恢复用，旧的没价值） ----

    def _prune_eventlog(self, t: float) -> None:
        with self.db.tx() as c:
            cur = c.execute("DELETE FROM event_log WHERE ts<?", (t - EVENTLOG_KEEP_DAYS * 86400,))
            self.stats["eventlog"] += cur.rowcount
