"""心脏：outbox 消费、CAS 领取、出站入队（方案 dafeiyu-plan 1.1/1.2/1.4）。

同步写路径决策（方案 1.2 轮7补）：outbox/heart_state/send_queue 的领取、CAS、
预扣是同步语义操作，这里全部走 `db.tx()`（内部全局写锁串行），不走异步 Writer
——Writer 只负责记忆沉淀，心脏的调度状态必须立刻可见。

触发三线中，本模块只实现"消费侧"（兜底扫描 + 事件驱动都调 `pump()`）：
- 事件到达：调用方先 `enqueue()`（毫秒级落 outbox），再调 `pump()` 立即消费
- 兜底扫描：外部定时器周期调 `pump()`，它自己会捡起超时的 pending/lease 过期行
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import params
from .db import DB
from .store import Ident

# 优先级：@/私聊/纠错(0) > 主动(1) > 采集(2) > 清理(3)
PRI_MENTION, PRI_PROACTIVE, PRI_INGEST, PRI_JANITOR = 0, 1, 2, 3

LEASE_SECONDS = params.HEART_LEASE_SECONDS    # processing 租约（方案 1.11 #8 互斥超时）
MAX_ATTEMPTS = params.HEART_MAX_ATTEMPTS      # 超限进死信
SCAN_BATCH = params.HEART_SCAN_BATCH          # 单次 pump 领取上限（批处理事务减少锁竞争）


@dataclass(slots=True)
class OutboxEvent:
    kind: str
    text: str = ""
    priority: int = PRI_MENTION
    extra: dict[str, Any] = field(default_factory=dict)


class SendThrottled(Exception):
    """The platform is temporarily full; keep the message pending."""

    def __init__(self, retry_at: float):
        super().__init__("send rate limit reached")
        self.retry_at = float(retry_at)

class Heart:
    def __init__(self, db: DB, handler: Callable[[Ident, OutboxEvent], str | None] | None = None,
                 sender: Callable[[Ident, str, dict[str, Any]], bool] | None = None,
                 instance_id: str | None = None):
        """
        handler: 消费函数——(ident, event) -> 回复文本（None 表示无需回复，如纯采集）。
        sender:  实际发送适配器——(ident, text, payload) -> 是否送达（成功才计 sent）。
        instance_id: 多实例认领标识（solid_queue 进程注册表语义）。None 时自动生成。
            心跳过期（HEART_INSTANCE_TTL）的实例，其名下 processing 行会被其他实例
            的 pump 批量退回 pending——崩溃恢复从 ≤LEASE_SECONDS 压到 ≤TTL+SCAN。
        """
        self.db = db
        self.handler = handler
        self.sender = sender
        self.instance_id = instance_id or f"heart-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._registered = False
        self.stats = {"enqueued": 0, "processed": 0, "failed": 0, "dead": 0, "sent": 0}

    # ---- 实例注册表（solid_queue claimed_processes 语义） ----

    def _heartbeat(self, now: float) -> None:
        """注册/续心跳。每次 pump/dispatch 开头调用。"""
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO instance(instance_id, kind, started_at, heartbeat_at)"
                " VALUES(?,?,?,?)"
                " ON CONFLICT(instance_id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at",
                (self.instance_id, "heart", now, now),
            )
        self._registered = True

    def reclaim_dead_instances(self, now: float, *, ttl: float | None = None) -> int:
        """心跳过期的实例 → 名下 processing 行批量退回 pending（含租约清空、退避不设——
        立即可被认领）。返回被接管的行数。崩溃恢复的快速路径。

        注意：仅处理 kind='heart' 的实例；janitor 有自己的租约（JANITOR_LEASE_TTL）。
        """
        ttl = params.HEART_INSTANCE_TTL if ttl is None else ttl
        n = 0
        with self.db.tx() as c:
            dead = [r["instance_id"] for r in c.execute(
                "SELECT instance_id FROM instance WHERE kind='heart' AND heartbeat_at<?",
                (now - ttl,),
            )]
            if not dead:
                return 0
            marks = ",".join("?" * len(dead))
            for table in ("outbox", "send_queue"):
                # send_queue 无 updated_at 列（v5 schema），按表分派
                if table == "outbox":
                    cur = c.execute(
                        f"UPDATE {table} SET status='pending', owner=NULL, lease_until=NULL,"
                        f" next_retry_at=0, updated_at=?"
                        f" WHERE status='processing' AND owner IN ({marks})",
                        (now, *dead),
                    )
                else:
                    cur = c.execute(
                        f"UPDATE {table} SET status='pending', owner=NULL, lease_until=NULL,"
                        f" next_retry_at=0"
                        f" WHERE status='processing' AND owner IN ({marks})",
                        (*dead,),
                    )
                n += cur.rowcount or 0
            c.execute(f"DELETE FROM instance WHERE instance_id IN ({marks})", dead)
        if n:
            print(f"[heart] 接管 {n} 行自死亡实例 {dead}", file=sys.stderr)
        return n

    # ---- 入队（触发第一线：事件到达毫秒级落 outbox） ----

    def enqueue(self, ident: Ident, ev: OutboxEvent, *, now: float | None = None) -> int | None:
        t = time.time() if now is None else now
        idem = ev.extra.get("idem_key") or (
            f"{ident.platform}|{ident.platform_user_id}|{ident.scope}|{ev.kind}|"
            f"{t}|{uuid.uuid4().hex}"
        )
        payload = dict(ev.extra)
        payload["text"] = ev.text  # text 进 payload，消费侧才能拿到
        rid = self._insert_outbox(ident, ev, idem, payload, t)
        if rid is not None:
            # 短期缓冲落盘（方案 1.3/1.2 恢复语义）：崩溃重启后按身份回放最近 N 条
            try:
                with self.db.tx() as c:
                    c.execute(
                        "INSERT INTO event_log(platform,platform_user_id,scope,kind,payload,ts)"
                        " VALUES(?,?,?,?,?,?)",
                        (ident.platform, ident.platform_user_id,
                         str(ident.scope or "default"), ev.kind,
                         json.dumps(payload, ensure_ascii=False), t),
                    )
            except Exception:
                pass  # 留痕失败不阻塞触发
        return rid

    def _retry_insert(self, ident: Ident, ev: OutboxEvent, idem: str, t: float) -> int | None:
        payload = dict(ev.extra)
        payload["text"] = ev.text
        return self._insert_outbox(ident, ev, idem, payload, t, retry_locked=False)

    def _insert_outbox(self, ident: Ident, ev: OutboxEvent, idem: str,
                       payload: dict, t: float, *, retry_locked: bool = True) -> int | None:
        try:
            with self.db.tx() as c:
                cur = c.execute(
                    "INSERT INTO outbox(platform,platform_user_id,scope,kind,priority,payload,"
                    "idem_key,status,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                    (ident.platform, ident.platform_user_id, str(ident.scope or "default"),
                     ev.kind, ev.priority, json.dumps(payload, ensure_ascii=False), idem, t, t),
                )
                self.stats["enqueued"] += 1
                return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            if "outbox.idem_key" in str(exc).lower():
                return None
            raise
        except sqlite3.OperationalError as exc:
            # 锁冲突不是幂等，短暂重试一次；其他数据库错误必须暴露给调用方。
            msg = str(exc).lower()
            if (("locked" in msg or "busy" in msg) and retry_locked):
                time.sleep(0.05)
                return self._retry_insert(ident, ev, idem, t)
            raise

    # ---- 消费（CAS 领取 → 处理 → 出站入队） ----

    def pump(self, *, now: float | None = None) -> int:
        """处理一批到期事件，返回处理条数。事件驱动与兜底扫描共用。"""
        t = time.time() if now is None else now
        self._heartbeat(t)
        self.reclaim_dead_instances(t)
        n = 0
        for _ in range(SCAN_BATCH):
            ev = self._claim(t)
            if ev is None:
                break
            row_id, ident, kind, priority, extra = ev
            try:
                reply = None
                if self.handler is not None:
                    reply = self.handler(ident, OutboxEvent(
                        kind=kind, text=extra.get("text", ""), priority=priority,
                        extra=extra))
                if reply:
                    # 方案 1.5 第 2 层：出口硬校验 fail-closed（gate 拦截不重试、进不了出站）
                    from .gate import check_reply
                    gate = check_reply(reply)
                    if gate.ok:
                        self._enqueue_send(
                            ident, gate.text, row_id, priority, t, kind=kind)
                self._finish(row_id, "done", t)
                self.stats["processed"] += 1
            except Exception:
                # 失败要留痕：静默退避是排查黑洞（本次 13 条 correction 积压即由此发现）
                import traceback
                print(f"[heart] outbox#{row_id} kind={kind} 处理失败:",
                      traceback.format_exc(limit=3), file=sys.stderr)
                self._finish(row_id, "failed", t, backoff=True)
                self.stats["failed"] += 1
            n += 1
        return n

    def _claim(self, now: float) -> tuple[int, Ident, str, int, dict] | None:
        """CAS 领取：pending 且到期，或 lease 过期的 processing（超时回收）。"""
        with self.db.tx() as c:
            row = c.execute(
                "SELECT id, platform, platform_user_id, scope, kind, priority, payload"
                " FROM outbox WHERE (status='pending' AND next_retry_at<=?)"
                "    OR (status='processing' AND lease_until<?)"
                " ORDER BY priority ASC, id ASC LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE outbox SET status='processing', lease_until=?, attempts=attempts+1,"
                " owner=?, updated_at=? WHERE id=? AND status IN ('pending','processing')",
                (now + LEASE_SECONDS, self.instance_id, now, row["id"]),
            )
            if c.total_changes and row["id"] is None:
                return None
            ident = Ident(row["platform"], row["platform_user_id"], scope=row["scope"])
            return int(row["id"]), ident, row["kind"], int(row["priority"]), json.loads(row["payload"])

    def _finish(self, row_id: int, status: str, now: float, *, backoff: bool = False) -> None:
        with self.db.tx() as c:
            if status == "failed" and backoff:
                row = c.execute("SELECT attempts FROM outbox WHERE id=?", (row_id,)).fetchone()
                attempts = int(row["attempts"]) if row else 1
                if attempts >= MAX_ATTEMPTS:
                    c.execute("UPDATE outbox SET status='dead', owner=NULL, updated_at=? WHERE id=?",
                              (now, row_id))
                    self.stats["dead"] += 1
                else:
                    delay = min(params.HEART_BACKOFF_CAP, 2.0 ** attempts)  # 指数退避封顶 60s
                    c.execute("UPDATE outbox SET status='pending', next_retry_at=?, lease_until=NULL,"
                              " owner=NULL, updated_at=? WHERE id=?", (now + delay, now, row_id))
            else:
                c.execute("UPDATE outbox SET status=?, lease_until=NULL, owner=NULL,"
                          " updated_at=? WHERE id=?", (status, now, row_id))

    # ---- 续租（goqite Extend 语义：长任务中途续租，防"正常处理但超 LEASE 被二次认领"） ----

    def extend_lease(self, row_id: int, *, now: float | None = None,
                     table: str = "outbox") -> bool:
        """把本实例名下 processing 行的租约延长 LEASE_SECONDS。

        只有 status='processing' 且 owner=本实例的行可续——续别人的（或已结束的）行
        返回 False 且不动数据（fencing：实例死过一次后不能再操作旧租约）。
        LLM 第二层的长任务（摘要/抽取可能 >120s）在 handler 内周期调用。
        """
        t = time.time() if now is None else now
        if table not in ("outbox", "send_queue"):
            raise ValueError(f"unknown lease table: {table}")
        with self.db.tx() as c:
            cur = c.execute(
                f"UPDATE {table} SET lease_until=?"
                f" WHERE id=? AND status='processing' AND owner=?",
                (t + LEASE_SECONDS, int(row_id), self.instance_id),
            )
            return bool(cur.rowcount)

    # ---- 出站队列（1.4：outbox done ≠ 送达，发送独立走 send_queue） ----

    def _enqueue_send(self, ident: Ident, text: str, group_id: int, priority: int, now: float,
                      kind: str = "reply", *, retry_locked: bool = True) -> int:
        send_idem = f"send|{ident.platform}|{ident.platform_user_id}|{ident.scope}|{group_id}"
        try:
            with self.db.tx() as c:
                cur = c.execute(
                    "INSERT INTO send_queue(platform,platform_user_id,scope,kind,priority,payload,"
                    "send_idem_key,status,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (ident.platform, ident.platform_user_id, str(ident.scope or "default"),
                     kind, max(priority, 0),
                     json.dumps({"text": text}, ensure_ascii=False), send_idem, "pending", now),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            if "send_queue.send_idem_key" in str(exc).lower():
                return 0
            raise
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if (("locked" in msg or "busy" in msg) and retry_locked):
                time.sleep(0.05)
                return self._enqueue_send(
                    ident, text, group_id, priority, now,
                    kind=kind, retry_locked=False,
                )
            raise

    def dispatch(self, *, now: float | None = None) -> int:
        """出站发送扫描：租约领取 → sender 送达 → sent。返回发送条数。"""
        t = time.time() if now is None else now
        self._heartbeat(t)
        self.reclaim_dead_instances(t)
        n = 0
        for _ in range(SCAN_BATCH):
            with self.db.tx() as c:
                row = c.execute(
                    "SELECT id, platform, platform_user_id, scope, payload, attempts"
                    " FROM send_queue"
                    " WHERE (status='pending' AND next_retry_at<=?)"
                    "    OR (status='processing' AND lease_until<?)"
                    " ORDER BY priority ASC, id ASC LIMIT 1",
                    (t, t),
                ).fetchone()
                if row is None:
                    break
                c.execute(
                    "UPDATE send_queue SET status='processing', lease_until=?, attempts=attempts+1,"
                    " owner=? WHERE id=? AND status IN ('pending','processing')",
                    (t + LEASE_SECONDS, self.instance_id, row["id"]),
                )
            ident = Ident(row["platform"], row["platform_user_id"], scope=row["scope"])
            payload = json.loads(row["payload"])
            ok = True
            throttled_until = None
            if self.sender is not None:
                try:
                    ok = bool(self.sender(ident, payload.get("text", ""), payload))
                except SendThrottled as exc:
                    ok = False
                    throttled_until = exc.retry_at
            with self.db.tx() as c:
                if throttled_until is not None:
                    c.execute(
                        "UPDATE send_queue SET status='pending', next_retry_at=?,"
                        " attempts=attempts-1, lease_until=NULL, owner=NULL WHERE id=?",
                        (max(t + 1.0, throttled_until), row["id"]),
                    )
                elif ok:
                    c.execute("UPDATE send_queue SET status='sent', lease_until=NULL, owner=NULL"
                              " WHERE id=?", (row["id"],))
                    self.stats["sent"] += 1
                else:
                    attempts = int(row["attempts"]) + 1
                    if attempts >= MAX_ATTEMPTS:
                        c.execute("UPDATE send_queue SET status='dead', owner=NULL WHERE id=?",
                                  (row["id"],))
                        self.stats["dead"] += 1
                    else:
                        delay = min(params.HEART_BACKOFF_CAP, 2.0 ** attempts)
                        c.execute("UPDATE send_queue SET status='pending', next_retry_at=?,"
                                  " lease_until=NULL, owner=NULL WHERE id=?", (t + delay, row["id"]))
            n += 1
        return n

    # ---- 恢复回放（方案 1.2 恢复语义） ----

    def replay_recent(self, ident: Ident, limit: int = 100) -> list[dict]:
        """崩溃重启后短期缓冲回放：该身份最近 N 条事件（按身份过滤，防串号）。

        返回按时间正序的 [{kind, payload, ts}]；调用方喂给短期缓冲（内存 deque）。
        只读不写：pending 的 outbox 行由兜底扫描自然捡起，这里不重复入队。
        """
        rows = self.db.read(
            "SELECT kind, payload, ts FROM event_log"
            " WHERE platform=? AND platform_user_id=? AND scope=?"
            " ORDER BY id DESC LIMIT ?",
            (ident.platform, ident.platform_user_id, str(ident.scope or "default"), limit),
        )
        return [dict(kind=r["kind"], payload=json.loads(r["payload"]), ts=r["ts"])
                for r in reversed(rows)]

    # ---- heart_state（1.6 额度预扣/退还；day-scoped 与 persistent 两类键） ----

    def state_get(self, ident: Ident, key: str, *, day_bucket: str = "0") -> Any:
        row = self.db.read(
            "SELECT value FROM heart_state WHERE platform=? AND platform_user_id=? AND scope=?"
            " AND state_key=? AND day_bucket=?",
            (ident.platform, ident.platform_user_id, str(ident.scope or "default"),
             key, day_bucket),
        )
        return json.loads(row[0]["value"]) if row else None

    def state_set(self, ident: Ident, key: str, value: Any, *, day_bucket: str = "0") -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO heart_state(platform,platform_user_id,scope,state_key,day_bucket,"
                "value,updated_at) VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(platform,platform_user_id,scope,state_key,day_bucket)"
                " DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (ident.platform, ident.platform_user_id, str(ident.scope or "default"),
                 key, day_bucket, json.dumps(value, ensure_ascii=False), time.time()),
            )
