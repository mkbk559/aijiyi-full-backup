"""SQLite 层：schema、按线程连接、写事务串行化、版本迁移。

多平台共存于**同一套表**：每张业务表都有
  platform          平台标识（wechat / douyin / cli / local …）
  platform_user_id  平台内的用户标识
  scope             统一命名的记忆域（personal / work / group:<id> …）
  extra             平台特有字段（TEXT，存 JSON），平台专属数据不进主列
身份主键统一是 (platform, platform_user_id)；scope 只是命名空间，不参与身份。

媒体只存**可重新获取的引用**（ref / url / mime / 过期时间），不存字节；
非文本输入的"降维结果"（OCR/ASR 文本、置信度、识别器、状态）也落在 media 行上。

v3 增补：fact.kind（fact|knowledge|relation）、fact 命中计数（反馈闭环）、
style 表（风格，软引导用，与人格核心隔离）、injection 表（注入可追溯）。

v4 增补（记忆质量，借鉴 Graphiti 的有效期 / A-MEM 的关联 / mem0 的多信号）：
fact.importance（写入时重要度，参与排序）、valid_from/valid_to（双时态有效期）、
mentions（重复提及次数）、fact_link（事实之间的无向关联）、fact_log（增删改审计）。

v9 增补：media 的唯一索引改为 (platform, platform_user_id, scope, ref)，避免同一
用户在多个群/scope 复用 ref 时串记录。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from aiji.right_hand import params

SCHEMA_VERSION = 12

# v10：角色自己的全局生活状态、事件轨迹和私密小本子。
SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS being_state(
  persona TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT '{}',
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS being_event(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  valence_delta REAL NOT NULL DEFAULT 0,
  energy_delta REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_being_event_persona
  ON being_event(persona, id DESC);
CREATE TABLE IF NOT EXISTS being_note(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  persona TEXT NOT NULL,
  text TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_being_note_persona
  ON being_note(persona, id DESC);
"""

# v8：WhaleGirl V5 生命卡（人格向量、四激素与离线时间锚点）。
SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS life_card(
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  card TEXT NOT NULL DEFAULT '{}',
  updated_at REAL NOT NULL,
  PRIMARY KEY (platform, platform_user_id, scope)
);
"""

# v6：实例注册表（solid_queue 进程注册表语义，2026-10-29 调研落点 P0）。
# instance = 心跳记录；outbox/send_queue.owner = 认领实例。
# 实例心跳过期 → 其名下 processing 行批量退回 pending（崩溃恢复 ≤TTL 而非 ≤LEASE_SECONDS）。
SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS instance(
  instance_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'heart',        -- heart | janitor（可扩展）
  started_at REAL NOT NULL,
  heartbeat_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_instance_heartbeat ON instance(kind, heartbeat_at);
"""

# v5：大肥鱼心脏层（方案 docs/agent/design/dafeiyu-plan.md 1.9）。
# outbox=触发事件入口；send_queue=出站发送（含租约）；heart_state=触发/额度状态
# （state_key 分两类：day-scoped 键随 day_bucket 保留，persistent 键 day_bucket 固定 0）；
# event_log=短期缓冲落盘（恢复回放用，必须带身份过滤）。全部走全局单写者。
SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  kind TEXT NOT NULL,                       -- mention | comment | correction | watch | proactive
  priority INTEGER NOT NULL DEFAULT 1,      -- 0=@/私聊/纠错 1=主动 2=采集 3=清理
  payload TEXT NOT NULL DEFAULT '{}',
  group_id INTEGER,                         -- 合并窗口：同 scope 组合事件共享
  idem_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending|processing|done|failed|dead
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at REAL NOT NULL DEFAULT 0,
  lease_until REAL,                         -- processing 租约，超时回收
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_idem ON outbox(idem_key);
CREATE INDEX IF NOT EXISTS idx_outbox_scan ON outbox(status, next_retry_at, priority, id);

CREATE TABLE IF NOT EXISTS send_queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  kind TEXT NOT NULL DEFAULT 'reply',
  priority INTEGER NOT NULL DEFAULT 1,
  payload TEXT NOT NULL DEFAULT '{}',
  send_idem_key TEXT NOT NULL,              -- = (scope, group_id)，见 1.11 #3
  status TEXT NOT NULL DEFAULT 'pending',   -- pending|processing|sent|failed|cancelled|dead
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at REAL NOT NULL DEFAULT 0,
  lease_until REAL,                         -- 1.4 轮1补：processing 租约，超时回收 pending
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sendq_idem ON send_queue(send_idem_key);
CREATE INDEX IF NOT EXISTS idx_sendq_scan ON send_queue(status, next_retry_at, priority, id);

CREATE TABLE IF NOT EXISTS heart_state(
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  state_key TEXT NOT NULL,
  day_bucket TEXT NOT NULL DEFAULT '0',     -- persistent 键固定 '0'；day-scoped 键存 'YYYY-MM-DD'
  value TEXT NOT NULL DEFAULT '{}',
  updated_at REAL NOT NULL,
  PRIMARY KEY (platform, platform_user_id, scope, state_key, day_bucket)
);

CREATE TABLE IF NOT EXISTS event_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eventlog_ident ON event_log(platform, platform_user_id, scope, id DESC);

CREATE INDEX IF NOT EXISTS idx_fact_status_id ON fact(status, id);
"""

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS account(
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  display_name TEXT,
  extra TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY (platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS fact(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
  text TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.7,
  status TEXT NOT NULL DEFAULT 'active',
  source TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL DEFAULT 'model',
  extra TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  superseded_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fact_ident ON fact(platform, platform_user_id, scope, status);
CREATE INDEX IF NOT EXISTS idx_fact_sp ON fact(platform, platform_user_id, scope, subject, predicate, status);
CREATE INDEX IF NOT EXISTS idx_fact_updated ON fact(updated_at DESC);

CREATE TABLE IF NOT EXISTS profile(
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.7,
  source_fact_id INTEGER,
  extra TEXT NOT NULL DEFAULT '{}',
  updated_at REAL NOT NULL,
  updates INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (platform, platform_user_id, scope, key)
);

CREATE TABLE IF NOT EXISTS turn(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  session TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  extra TEXT NOT NULL DEFAULT '{}',
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_session
  ON turn(platform, platform_user_id, scope, session, id DESC);

CREATE TABLE IF NOT EXISTS summary(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  session TEXT NOT NULL,
  upto_turn INTEGER NOT NULL,
  text TEXT NOT NULL,
  extra TEXT NOT NULL DEFAULT '{}',
  ts REAL NOT NULL
);

-- 媒体只存引用：ref 是平台侧可再取的句柄（消息 id / file_id / url），url 可带 TTL。
-- 没有任何 BLOB 列，这是有意的：文件归平台，这里只记"去哪重新拿"。
CREATE TABLE IF NOT EXISTS media(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  session TEXT,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  url TEXT,
  mime TEXT,
  size_bytes INTEGER,
  expires_at REAL,
  extra TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_media_ref ON media(platform, platform_user_id, scope, ref);

CREATE TABLE IF NOT EXISTS write_queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL DEFAULT 'local',
  platform_user_id TEXT NOT NULL DEFAULT 'default',
  scope TEXT NOT NULL DEFAULT 'default',
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at REAL NOT NULL,
  next_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_wq_state ON write_queue(state, next_at);

CREATE VIRTUAL TABLE IF NOT EXISTS fact_fts USING fts5(
  subject, predicate, object, text, content='fact', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS fact_ai AFTER INSERT ON fact BEGIN
  INSERT INTO fact_fts(rowid, subject, predicate, object, text)
  VALUES (new.id, new.subject, new.predicate, new.object, new.text);
END;
CREATE TRIGGER IF NOT EXISTS fact_au AFTER UPDATE ON fact BEGIN
  INSERT INTO fact_fts(fact_fts, rowid, subject, predicate, object, text)
  VALUES('delete', old.id, old.subject, old.predicate, old.object, old.text);
  INSERT INTO fact_fts(rowid, subject, predicate, object, text)
  VALUES (new.id, new.subject, new.predicate, new.object, new.text);
END;
CREATE TRIGGER IF NOT EXISTS fact_ad AFTER DELETE ON fact BEGIN
  INSERT INTO fact_fts(fact_fts, rowid, subject, predicate, object, text)
  VALUES('delete', old.id, old.subject, old.predicate, old.object, old.text);
END;
"""

# v3：学习/总结/注入三件事落地所需的最小增补。ALTER 由 _migrate_v2_to_v3 负责，
# 这里只放新建的表（不管老库新库，都是 IF NOT EXISTS）。
SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS style(
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.6,
  evidence INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL,
  PRIMARY KEY (platform, platform_user_id, scope, dimension)
);

-- 每次注入留痕：注入了哪些条目、主模型是否采纳（反馈闭环）。
CREATE TABLE IF NOT EXISTS injection(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  session TEXT,
  query TEXT NOT NULL,
  item_ids TEXT NOT NULL DEFAULT '[]',
  prompt TEXT NOT NULL DEFAULT '',
  feedback TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_injection_ident
  ON injection(platform, platform_user_id, scope, id DESC);
CREATE INDEX IF NOT EXISTS idx_fact_kind
  ON fact(platform, platform_user_id, scope, kind, status);
"""

# v7：自我层。物理底线是代码常量（aiji/selfhood.py），**不进库**——
# 库里只放"从互动里涌现出来的性格"，所以这些行天然可被新证据推翻。
SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS trait(
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  name TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5,
  evidence INTEGER NOT NULL DEFAULT 1,
  origin TEXT NOT NULL DEFAULT 'reflect',
  updated_at REAL NOT NULL,
  PRIMARY KEY (platform, platform_user_id, scope, name)
);
CREATE INDEX IF NOT EXISTS idx_trait_ident
  ON trait(platform, platform_user_id, scope, confidence DESC);

-- 每次反思留痕：up_to_turn 是幂等游标（记住已反思到哪条 turn），
-- deltas 是本轮 trait 变更 JSON，可回溯"我是什么时候变成这样的"。
CREATE TABLE IF NOT EXISTS reflection(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  session TEXT NOT NULL DEFAULT 'default',
  up_to_turn INTEGER NOT NULL DEFAULT 0,
  turns_seen INTEGER NOT NULL DEFAULT 0,
  summary TEXT NOT NULL DEFAULT '',
  deltas TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reflection_ident
  ON reflection(platform, platform_user_id, scope, session, id DESC);
"""

# v4：记忆质量。fact 增重要度/有效期/提及数；新增关联表与审计表。
# valid_from/valid_to 对应 Graphiti 的“事实有效期”——失效只打时间戳，不删行。
SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS fact_link(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  src_id INTEGER NOT NULL,
  dst_id INTEGER NOT NULL,
  weight REAL NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  UNIQUE(platform, platform_user_id, scope, src_id, dst_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_link_src
  ON fact_link(platform, platform_user_id, scope, src_id);
CREATE INDEX IF NOT EXISTS idx_fact_link_dst
  ON fact_link(platform, platform_user_id, scope, dst_id);

-- 每次写入决策留痕：add | merge | supersede | expire（否定失效）。
CREATE TABLE IF NOT EXISTS fact_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  op TEXT NOT NULL,
  fact_id INTEGER NOT NULL,
  target_id INTEGER,
  detail TEXT NOT NULL DEFAULT '{}',
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fact_log_ident
  ON fact_log(platform, platform_user_id, scope, id DESC);
"""


SCHEMA_V11 = """
-- v11：记忆代谢（borrow-plan 2026-11-02）
-- ① turn 原文归档：压缩淘汰前复制到 turn_archive，退役不删（90 天后才物理清）。
CREATE TABLE IF NOT EXISTS turn_archive(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  platform_user_id TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'default',
  session TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  extra TEXT NOT NULL DEFAULT '{}',
  ts REAL NOT NULL,
  archived_at REAL NOT NULL,
  summary_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turn_archive_ident
  ON turn_archive(platform, platform_user_id, scope, session, id DESC);
CREATE INDEX IF NOT EXISTS idx_turn_archive_ts ON turn_archive(archived_at);

-- ② 自我记忆（手册 v5 第五层）：她自己的事，SELF_MEMORY_MAX 上限、淘汰最久没用。
CREATE TABLE IF NOT EXISTS self_memory(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL DEFAULT '*',
  platform_user_id TEXT NOT NULL DEFAULT '*',
  scope TEXT NOT NULL DEFAULT 'default',
  kind TEXT NOT NULL DEFAULT 'pref',
  text TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5,
  hit_count INTEGER NOT NULL DEFAULT 0,
  last_used_at REAL,
  created_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_self_memory_ident
  ON self_memory(platform, platform_user_id, scope, status, id DESC);
"""


SCHEMA_V12 = """
-- v12：社交连续性（"续火花"，2026-11-05 三平台社交闭环）
-- 一次"互动"= 与某个 peer 发生了一轮真实来回（我们发或对方发都算），
-- 连续天数按**本地日期**算；断一天就归零，但 best 留着（历史最长）。
CREATE TABLE IF NOT EXISTS streak(
  platform TEXT NOT NULL,
  peer TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'friend',
  last_day TEXT NOT NULL,
  days INTEGER NOT NULL DEFAULT 1,
  best INTEGER NOT NULL DEFAULT 1,
  updated_at REAL NOT NULL,
  PRIMARY KEY (platform, peer, kind)
);
CREATE INDEX IF NOT EXISTS idx_streak_last_day ON streak(platform, last_day);
"""


class DB:
    """每个线程一条连接；写事务全局串行。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._init_lock = threading.Lock()
        self._connections_lock = threading.Lock()
        self._connections: set[sqlite3.Connection] = set()
        self._ready = False
        self.epoch = 0  # 每次提交写事务 +1，供召回侧判断快照是否过期
        # 构造即初始化 schema：消除"两个线程并发首调 conn() 时，
        # 一个在 _init_schema 长事务里、另一个的写撞 database is locked"的竞态。
        # （dispatcher 线程先于主线程启动时真实发生过，10 次里 2-6 次失败。）
        self.conn()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is not None:
            return c
        # Connections are still owned per thread during normal operation.  The
        # relaxed thread check only lets the owner object close all remaining
        # handles after its workers have stopped (required on Windows).
        c = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute(f"PRAGMA busy_timeout={params.BUSY_TIMEOUT_MS}")
        with self._init_lock:
            if not self._ready:
                self._init_schema(c)
                self._ready = True
            # 双重检查：别的线程刚初始化完，本连接虽然跳过了 DDL，
            # 但必须确认 user_version 已生效（sqlite 每连接独立视图）。
        self._local.conn = c
        with self._connections_lock:
            self._connections.add(c)
        return c

    def _init_schema(self, c: sqlite3.Connection) -> None:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        has_fact = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact'"
        ).fetchone()
        if version >= SCHEMA_VERSION:
            return
        if version == 1 or (version == 0 and has_fact):
            self._migrate_v1_to_v2(c)
            # v1 的行是 ALTER 加列来的，FTS 里没有它们，必须重建索引
            c.executescript(SCHEMA_V2)
            c.execute("INSERT INTO fact_fts(fact_fts) VALUES('rebuild')")
        else:
            c.executescript(SCHEMA_V2)
        self._migrate_v2_to_v3(c)
        self._migrate_v3_to_v4(c)
        self._migrate_v4_to_v5(c)
        self._migrate_v5_to_v6(c)
        self._migrate_v6_to_v7(c)
        self._migrate_v7_to_v8(c)
        self._migrate_v8_to_v9(c)
        self._migrate_v9_to_v10(c)
        self._migrate_v10_to_v11(c)
        self._migrate_v11_to_v12(c)
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        c.commit()

    @staticmethod
    def _migrate_v11_to_v12(c: sqlite3.Connection) -> None:
        """v11 → v12：社交连续性账本（续火花）。只加表，不动既有表。"""
        c.executescript(SCHEMA_V12)
        c.commit()

    @staticmethod
    def _migrate_v7_to_v8(c: sqlite3.Connection) -> None:
        c.executescript(SCHEMA_V8)
        c.commit()

    @staticmethod
    def _migrate_v8_to_v9(c: sqlite3.Connection) -> None:
        """Scope-aware media uniqueness: the same ref in two scopes is two rows."""
        c.executescript(
            """
            DROP INDEX IF EXISTS ux_media_ref;
            CREATE UNIQUE INDEX ux_media_ref
              ON media(platform, platform_user_id, scope, ref);
            """
        )
        c.commit()

    @staticmethod
    def _migrate_v10_to_v11(c: sqlite3.Connection) -> None:
        """v10 → v11：记忆代谢层（稳定性/冻结/原文归档/自我记忆/摘要哈希）。

        - `fact.stability/difficulty/frozen`：FSRS-5 门禁（冻结 ≠ 删除，查到即解冻）；
        - `turn_archive`：压缩淘汰前把原文复制过来（退役不删）；
        - `self_memory`：手册 v5 第五层（她自己的事，200 条上限）；
        - `summary.source_hash/source_turn_ids`：同哈希复用，省模型调用；摘要可指回原文。

        只 ADD COLUMN，不重建表（避免 FTS 与既有索引被牵动）。
        """
        from aiji.right_hand import params as _p

        c.executescript(SCHEMA_V11)
        have = {r[1] for r in c.execute("PRAGMA table_info(fact)")}
        adds = (
            ("stability", f"REAL NOT NULL DEFAULT {_p.STABILITY_INIT_DAYS}"),
            ("difficulty", "REAL NOT NULL DEFAULT 5.0"),
            ("frozen", "INTEGER NOT NULL DEFAULT 0"),
        )
        for col, ddl in adds:
            if col not in have:
                c.execute(f"ALTER TABLE fact ADD COLUMN {col} {ddl}")
        # 初始稳定性按 kind 分档（memory-decay.js initialStability）：情景记忆更短
        c.execute(
            "UPDATE fact SET stability=? WHERE kind='episodic' AND stability=?",
            (14.0, _p.STABILITY_INIT_DAYS),
        )
        c.execute(
            "UPDATE fact SET stability=? WHERE kind='relation' AND stability=?",
            (30.0, _p.STABILITY_INIT_DAYS),
        )
        have_s = {r[1] for r in c.execute("PRAGMA table_info(summary)")}
        for col in ("source_hash", "source_turn_ids"):
            if col not in have_s:
                c.execute(f"ALTER TABLE summary ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        c.commit()

    @staticmethod
    def _migrate_v9_to_v10(c: sqlite3.Connection) -> None:
        """v9 → v10: global being state, life events and private notes."""
        c.executescript(SCHEMA_V10)
        c.commit()

    @staticmethod
    def _migrate_v6_to_v7(c: sqlite3.Connection) -> None:
        """v6 → v7：自我层两表（trait 涌现性格 + reflection 反思留痕）。"""
        c.executescript(SCHEMA_V7)
        c.commit()

    @staticmethod
    def _migrate_v2_to_v3(c: sqlite3.Connection) -> None:
        """v2 → v3：给 fact/media 加学习与识别所需列，新建 style / injection。"""
        adds = {
            "fact": (
                ("kind", "TEXT NOT NULL DEFAULT 'fact'"),
                ("hit_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_used_at", "REAL"),
            ),
            "media": (
                ("text", "TEXT"),
                ("confidence", "REAL"),
                ("status", "TEXT NOT NULL DEFAULT 'pending'"),
                ("recognizer", "TEXT"),
                ("elapsed_ms", "REAL"),
            ),
        }
        for table, cols in adds.items():
            exist = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exist:
                continue
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, ddl in cols:
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        c.executescript(SCHEMA_V3)
        c.commit()

    @staticmethod
    def _migrate_v3_to_v4(c: sqlite3.Connection) -> None:
        """v3 → v4：事实增加重要度/有效期/提及数，新建关联表与审计表。

        valid_from 回填 created_at；已失效的旧行 valid_to 回填 updated_at（旧值最后一次变更时间）。
        """
        have = {r[1] for r in c.execute("PRAGMA table_info(fact)")}
        adds = (
            ("importance", "REAL NOT NULL DEFAULT 0.5"),
            ("mentions", "INTEGER NOT NULL DEFAULT 1"),
            ("valid_from", "REAL"),
            ("valid_to", "REAL"),
        )
        for col, ddl in adds:
            if col not in have:
                c.execute(f"ALTER TABLE fact ADD COLUMN {col} {ddl}")
        c.executescript(SCHEMA_V4)
        c.execute("UPDATE fact SET valid_from=created_at WHERE valid_from IS NULL")
        c.execute(
            "UPDATE fact SET valid_to=updated_at"
            " WHERE valid_to IS NULL AND status!='active'"
        )
        c.commit()

    @staticmethod
    def _migrate_v5_to_v6(c: sqlite3.Connection) -> None:
        """v5 → v6：实例注册表 + outbox/send_queue.owner 认领列。"""
        c.executescript(SCHEMA_V6)
        for table in ("outbox", "send_queue"):
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            if "owner" not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN owner TEXT")
        c.commit()

    @staticmethod
    def _migrate_v4_to_v5(c: sqlite3.Connection) -> None:
        """v4 → v5：心脏层四表 + fact 乐观锁列（方案 1.9：并入 fact 加列，不建独立版本表）。"""
        c.executescript(SCHEMA_V5)
        have = {r[1] for r in c.execute("PRAGMA table_info(fact)")}
        if "version" not in have:
            c.execute("ALTER TABLE fact ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        c.commit()

    @staticmethod
    def _migrate_v1_to_v2(c: sqlite3.Connection) -> None:
        """v1（单平台、无 platform/scope/extra）→ v2：老数据一律归到 local/default。"""
        ddl = {
            "platform": "TEXT NOT NULL DEFAULT 'local'",
            "platform_user_id": "TEXT NOT NULL DEFAULT 'default'",
            "scope": "TEXT NOT NULL DEFAULT 'default'",
            "extra": "TEXT NOT NULL DEFAULT '{}'",
        }
        targets = (
            ("fact", ("platform", "platform_user_id", "scope", "extra")),
            ("turn", ("platform", "platform_user_id", "scope", "extra")),
            ("summary", ("platform", "platform_user_id", "scope", "extra")),
            ("write_queue", ("platform", "platform_user_id", "scope")),
        )
        for table, cols in targets:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                continue
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for col in cols:
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl[col]}")
        # profile 的主键从 key 换成 (platform, platform_user_id, scope, key)，只能重建
        old = c.execute("PRAGMA table_info(profile)").fetchall()
        if old and old[0][1] == "key":
            c.executescript(
                """
                ALTER TABLE profile RENAME TO profile_v1;
                CREATE TABLE profile(
                  platform TEXT NOT NULL,
                  platform_user_id TEXT NOT NULL,
                  scope TEXT NOT NULL DEFAULT 'default',
                  key TEXT NOT NULL,
                  value TEXT NOT NULL,
                  confidence REAL NOT NULL DEFAULT 0.7,
                  source_fact_id INTEGER,
                  extra TEXT NOT NULL DEFAULT '{}',
                  updated_at REAL NOT NULL,
                  updates INTEGER NOT NULL DEFAULT 1,
                  PRIMARY KEY (platform, platform_user_id, scope, key)
                );
                INSERT INTO profile(platform, platform_user_id, scope, key, value, confidence,
                                    source_fact_id, updated_at, updates)
                SELECT 'local', 'default', 'default', key, value, confidence,
                       source_fact_id, updated_at, updates FROM profile_v1;
                DROP TABLE profile_v1;
                """
            )
        c.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self.conn()
        with self._write_lock:
            try:
                yield c
                c.commit()
                self.epoch += 1
            except BaseException:
                c.rollback()
                raise

    def read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, args).fetchall()

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            with self._connections_lock:
                self._connections.discard(c)
            self._local.conn = None

    def close_all(self) -> None:
        """Close handles created by worker threads after those workers stop."""
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self._local.conn = None
