"""生产库完整性探测（只读 immutable 打开 data/aiji.db，不写任何东西）。"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from tools.probe._harness import Probe  # noqa: E402

probe = Probe("prod-db")
DB_PATH = pathlib.Path(r"D:\AIJiYi\data\aiji.db")
if not DB_PATH.exists():
    probe.case("生产库存在", False, str(DB_PATH))
    raise SystemExit(probe.finish())

con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()


def one(sql: str, args=()):
    row = cur.execute(sql, args).fetchone()
    return row[0] if row else None


try:
    version = one("PRAGMA user_version")
    probe.case("schema 版本 = 10", version == 10, f"user_version={version}")
    probe.case("生产库可读且非空", one("SELECT COUNT(*) FROM fact") > 0,
               f"facts={one('SELECT COUNT(*) FROM fact')} bytes={DB_PATH.stat().st_size}")

    # --- 外键/孤儿 ---
    orphan_links = one(
        "SELECT COUNT(*) FROM fact_link fl"
        " LEFT JOIN fact a ON a.id=fl.src_id LEFT JOIN fact b ON b.id=fl.dst_id"
        " WHERE a.id IS NULL OR b.id IS NULL")
    probe.case("fact_link 无孤儿边", orphan_links == 0, f"orphans={orphan_links}")
    cross_ident = one(
        "SELECT COUNT(*) FROM fact_link fl JOIN fact a ON a.id=fl.src_id"
        " JOIN fact b ON b.id=fl.dst_id"
        " WHERE a.platform<>b.platform OR a.platform_user_id<>b.platform_user_id"
        " OR a.scope<>b.scope")
    probe.case("关联边不跨身份", cross_ident == 0, f"cross={cross_ident}")
    orphan_logs = one("SELECT COUNT(*) FROM fact_log l LEFT JOIN fact f ON f.id=l.fact_id"
                      " WHERE l.fact_id<>0 AND f.id IS NULL")
    probe.case("fact_log 指向的事实都在（软删不物理删）", orphan_logs == 0, f"orphans={orphan_logs}")
    orphan_turns = one("SELECT COUNT(*) FROM turn t LEFT JOIN account a"
                       " ON a.platform=t.platform AND a.platform_user_id=t.platform_user_id"
                       " WHERE a.platform IS NULL")
    probe.case("turn 都有对应 account", orphan_turns == 0, f"orphans={orphan_turns}")

    # --- 状态一致性 ---
    bad_active = one("SELECT COUNT(*) FROM fact WHERE status='active' AND valid_to IS NOT NULL")
    probe.case("active 行没有 valid_to", bad_active == 0, f"bad={bad_active}")
    bad_super = one("SELECT COUNT(*) FROM fact WHERE status='superseded'"
                    " AND (superseded_by IS NULL OR valid_to IS NULL)")
    probe.case("superseded 行都有 superseded_by 和 valid_to", bad_super == 0, f"bad={bad_super}")
    bad_expired = one("SELECT COUNT(*) FROM fact WHERE status='expired' AND valid_to IS NULL")
    probe.case("expired 行都有 valid_to", bad_expired == 0, f"bad={bad_expired}")
    bad_window = one("SELECT COUNT(*) FROM fact WHERE valid_to IS NOT NULL"
                     " AND valid_from IS NOT NULL AND valid_to < valid_from")
    probe.case("有效期窗口不倒挂（valid_to ≥ valid_from）", bad_window == 0, f"bad={bad_window}")

    # --- 单值谓词重复 ---
    dup = one(
        "SELECT COUNT(*) FROM ("
        " SELECT platform,platform_user_id,scope,subject,predicate,kind,COUNT(*) c"
        " FROM fact WHERE status='active'"
        " AND predicate IN ('居住地','职业','年龄','邮箱','电话','名字','出生地')"
        " GROUP BY platform,platform_user_id,scope,subject,predicate,kind HAVING c>1)")
    probe.case("单值谓词没有多条 active 并存", dup == 0, f"groups={dup}")

    # --- 跨平台隔离抽样：同一 QQ 号在不同 scope 的昵称解析 ---
    scopes_per_user = cur.execute(
        "SELECT platform_user_id, COUNT(DISTINCT scope) c FROM fact GROUP BY platform,platform_user_id"
        " HAVING c>1 ORDER BY c DESC LIMIT 1").fetchone()
    probe.case("存在跨 scope 的多身份（群 vs 私聊）",
                bool(scopes_per_user) and scopes_per_user["c"] >= 1,
                f"user={scopes_per_user['platform_user_id'] if scopes_per_user else None}"
                f" scopes={scopes_per_user['c'] if scopes_per_user else 0}")

    # --- 媒体识别现状 ---
    image_rows = one("SELECT COUNT(*) FROM media WHERE kind='image'")
    image_empty = one("SELECT COUNT(*) FROM media WHERE kind='image'"
                      " AND COALESCE(text,'')=''")
    probe.case("图片识别缺口量化（空文本占比）",
                True, f"image={image_rows} empty={image_empty} "
                      f"ratio={(image_empty / image_rows) if image_rows else 0:.0%}")
    voice_ok = one("SELECT COUNT(*) FROM media WHERE kind='voice' AND status='ok'")
    probe.case("语音识别有成功样本", (voice_ok or 0) > 0, f"voice_ok={voice_ok}")

    # --- 反馈闭环 ---
    no_feedback = one("SELECT COUNT(*) FROM injection WHERE feedback IS NULL")
    total_inj = one("SELECT COUNT(*) FROM injection")
    probe.case("注入留痕存在", (total_inj or 0) > 0, f"injection={total_inj}")
    probe.case("反馈闭环现状（未回填占比）", True,
               f"feedback_null={no_feedback}/{total_inj}")

    # --- 摘要/压缩 ---
    summaries = one("SELECT COUNT(*) FROM summary")
    max_turn = one("SELECT COUNT(*) FROM turn")
    probe.case("摘要与原文并存", (summaries or 0) > 0 and (max_turn or 0) > 0,
               f"summary={summaries} turn={max_turn}")
    dup_summary = one("SELECT COUNT(*) FROM (SELECT platform,platform_user_id,scope,session,"
                      "upto_turn,COUNT(*) c FROM summary GROUP BY 1,2,3,4,5 HAVING c>1)")
    probe.case("摘要幂等（无重复 upto_turn）", dup_summary == 0, f"dups={dup_summary}")

    # --- 队列/心脏层 ---
    for table in ("outbox", "send_queue", "write_queue", "event_log", "heart_state"):
        count = one(f"SELECT COUNT(*) FROM {table}")
        probe.case(f"队列表 {table} 可读", True, f"rows={count}")

    # --- 风格/性格数值范围 ---
    bad_style = one("SELECT COUNT(*) FROM style WHERE confidence<0 OR confidence>1")
    probe.case("style 置信度在 [0,1]", bad_style == 0, f"bad={bad_style}")
    bad_trait = one("SELECT COUNT(*) FROM trait WHERE confidence<0 OR confidence>1")
    probe.case("trait 置信度在 [0,1]", bad_trait == 0, f"bad={bad_trait}")
    bad_media = one("SELECT COUNT(*) FROM media WHERE confidence<0 OR confidence>1")
    probe.case("media 置信度在 [0,1]", bad_media == 0, f"bad={bad_media}")

    # --- 体积 ---
    wal = DB_PATH.with_name(DB_PATH.name + "-wal")
    probe.case("WAL 体积可控（<64MB）",
                (not wal.exists()) or wal.stat().st_size < 64 * 1024 * 1024,
                f"wal={(wal.stat().st_size / 1e6) if wal.exists() else 0:.1f}MB "
                f"db={DB_PATH.stat().st_size / 1e6:.1f}MB")

    # --- 一致性快检 ---
    t0 = time.time()
    cur.execute("SELECT COUNT(*) FROM fact").fetchone()
    probe.case("大表计数可读（不锁死）", True, f"{time.time() - t0:.3f}s")
finally:
    con.close()

raise SystemExit(probe.finish())
