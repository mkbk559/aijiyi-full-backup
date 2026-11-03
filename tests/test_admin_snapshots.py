"""控制台数据面（`right_hand/admin.py`）——70% → 全覆盖。

这些都是"把内核表拼成 UI JSON"的只读函数，容易错的点：
- 身份隔离：A 的快照绝不能带出 B 的行（串号 = 隐私事故）
- 容错：extra/payload 是 JSON 字符串也可能是 NULL，坏值必须回默认而不是崩
- 边裁剪：memory_graph 的边必须两端都在返回的节点集里，否则前端画悬空边
"""
import json
import pathlib
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident, add_fact  # noqa: E402
from aiji.right_hand import admin  # noqa: E402


def make_db(testcase) -> tuple[DB, Ident]:
    db = DB(make_tmp(testcase, "admin") / "a.db")
    ident = Ident("wechat", "u1", "personal")
    return db, ident


class MemoryLibraryTest(unittest.TestCase):
    def test_lists_active_facts_for_identity_only(self):
        db, ident = make_db(self)
        other = Ident("qq", "u1", "default")
        with db.tx() as conn:
            add_fact(conn, ident, FactDraft("user", "昵称", "玛卡巴卡", "称呼", 0.9))
            add_fact(conn, other, FactDraft("user", "昵称", "别人", "串号检测", 0.9))
        out = admin.memory_library(db, ident)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["object"], "玛卡巴卡")
        self.assertEqual(out["identity"]["platform"], "wechat")

    def test_query_searches(self):
        db, ident = make_db(self)
        with db.tx() as conn:
            add_fact(conn, ident, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
            add_fact(conn, ident, FactDraft("user", "职业", "厨师", "用户做饭", 0.9))
        out = admin.memory_library(db, ident, query="杭州")
        objects = [item["object"] for item in out["items"]]
        self.assertIn("杭州", objects)
        self.assertNotIn("厨师", objects)

    def test_fact_row_tolerates_nulls_and_bad_extra(self):
        db, ident = make_db(self)
        with db.tx() as conn:
            fact_id, _ = add_fact(conn, ident, FactDraft("user", "x", "y", "文本", 0.9))
            # 这些列在 schema 里都是 NOT NULL（`or` 是给老库/外部行留的防御），
            # 所以这里只能造"坏 JSON + 空 kind + 0 值"，不能真塞 NULL。
            conn.execute("UPDATE fact SET extra='不是json', importance=0,"
                         " mentions=0, hit_count=0, kind='' WHERE id=?",
                         (fact_id,))
        out = admin.memory_library(db, ident)
        row = out["items"][0]
        self.assertEqual(row["extra"], {}, "坏 extra 要回空 dict，不是崩")
        self.assertEqual(row["importance"], 0)
        self.assertEqual(row["kind"], "fact", "kind 空要补默认")
        self.assertEqual(row["mentions"], 1, "mentions 空要补 1")
        self.assertEqual(row["hit_count"], 0)


class MemoryGraphTest(unittest.TestCase):
    def test_edges_are_clipped_to_returned_nodes(self):
        db, ident = make_db(self)
        with db.tx() as conn:
            first, _ = add_fact(conn, ident, FactDraft("user", "a", "1", "事实一", 0.9))
            second, _ = add_fact(conn, ident, FactDraft("user", "b", "2", "事实二", 0.9))
            hidden, _ = add_fact(conn, ident, FactDraft("user", "c", "3", "事实三", 0.1))
            conn.execute(
                "INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, ident.scope,
                 first, hidden, 0.9, "悬空", time.time()),
            )
            conn.execute(
                "INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, ident.scope,
                 first, second, 0.5, "正常", time.time()),
            )
        out = admin.memory_graph(db, ident, limit=2)   # 只返回两个最重要节点
        ids = {node["id"] for node in out["nodes"]}
        self.assertNotIn(hidden, ids, "低重要性的不该进前两名")
        for edge in out["edges"]:
            self.assertIn(edge["source"], ids, "边两端必须在节点集里")
            self.assertIn(edge["target"], ids)


class SchedulerSnapshotTest(unittest.TestCase):
    def test_counts_and_recent_rows(self):
        db, ident = make_db(self)
        now = time.time()
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO outbox(platform,platform_user_id,scope,kind,priority,"
                "status,attempts,created_at,updated_at,payload,idem_key)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, ident.scope,
                 "sticker", 1, "done", 0, now, now,
                 json.dumps({"k": "v"}), "idem-1"),
            )
            conn.execute(
                "INSERT INTO send_queue(platform,platform_user_id,scope,kind,priority,"
                "status,attempts,created_at,payload,send_idem_key)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, ident.scope,
                 "text", 2, "pending", 1, now, "{坏掉的json", "send-1"),
            )
        out = admin.scheduler_snapshot(db, ident)
        self.assertEqual(out["outbox"], {"done": 1})
        self.assertEqual(out["send_queue"], {"pending": 1})
        self.assertEqual(out["recent_outbox"][0]["payload"], {"k": "v"})
        self.assertEqual(out["recent_send"][0]["payload"], {}, "坏 payload 回空 dict")
        self.assertEqual(out["recent_send"][0]["attempts"], 1)

    def test_none_identity_shows_everything(self):
        db, ident = make_db(self)
        now = time.time()
        with db.tx() as conn:
            for platform in ("wechat", "qq"):
                conn.execute(
                    "INSERT INTO send_queue(platform,platform_user_id,scope,kind,priority,"
                    "status,attempts,created_at,payload,send_idem_key)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (platform, "x", "default", "text", 1, "pending", 0, now, "{}",
                     f"send-{platform}"),
                )
        out = admin.scheduler_snapshot(db, None)
        self.assertEqual(len(out["recent_send"]), 2)


class ModelsSnapshotTest(unittest.TestCase):
    def test_counts_active_and_cooldown(self):
        out = admin.models_snapshot([
            {"name": "a", "cooldown_s": 0},
            {"name": "b", "cooldown_s": 30},
            {"name": "c"},                      # 缺 cooldown 字段 → 视为可用
        ])
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["active"], 2)
        self.assertEqual(out["cooldown"], 1)
        self.assertEqual(out["routes"][0]["route"], "a", "route 兜底用 name")


class RecentTurnsSnapshotTest(unittest.TestCase):
    def test_with_and_without_session(self):
        db, ident = make_db(self)
        now = time.time()
        with db.tx() as conn:
            for text in ("你好", "在吗"):
                conn.execute(
                    "INSERT INTO turn(platform,platform_user_id,scope,session,role,"
                    "content,ts,extra) VALUES(?,?,?,?,?,?,?,?)",
                    (ident.platform, ident.platform_user_id, ident.scope,
                     "s1", "user", text, now, json.dumps({"a": 1})),
                )
            conn.execute(
                "INSERT INTO turn(platform,platform_user_id,scope,session,role,"
                "content,ts,extra) VALUES(?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, ident.scope,
                 "s2", "user", "别的会话", now, ""),
            )
        by_session = admin.recent_turns_snapshot(db, ident, session="s1")
        self.assertEqual(len(by_session["items"]), 2)
        everything = admin.recent_turns_snapshot(db, ident)
        self.assertEqual(len(everything["items"]), 3)
        null_extra = [i for i in everything["items"] if i["content"] == "别的会话"][0]
        self.assertEqual(null_extra["extra"], {}, "NULL extra 要回空 dict")


class InjectionSnapshotTest(unittest.TestCase):
    def test_lists_injections_with_parsed_json_fields(self):
        db, ident = make_db(self)
        now = time.time()
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO injection(platform,platform_user_id,scope,session,query,"
                "item_ids,prompt,feedback,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, ident.scope, "s1",
                 "在做什么", json.dumps([1, 2]), "prompt 文本",
                 json.dumps({"helpful": True}), now),
            )
        out = admin.injection_snapshot(db, ident)
        item = out["items"][0]
        self.assertEqual(item["item_ids"], [1, 2])
        self.assertEqual(item["feedback"], {"helpful": True})

    def test_empty_feedback_stays_none(self):
        db, ident = make_db(self)
        now = time.time()
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO injection(platform,platform_user_id,scope,session,query,"
                "item_ids,prompt,feedback,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (ident.platform, ident.platform_user_id, ident.scope, "s1",
                 "q", "[]", "p", "", now),
            )
        out = admin.injection_snapshot(db, ident)
        self.assertIsNone(out["items"][0]["feedback"])


class ProactiveSnapshotTest(unittest.TestCase):
    def test_groups_by_identity(self):
        db, ident = make_db(self)
        other = Ident("qq", "u2", "default")
        now = time.time()
        with db.tx() as conn:
            for i, (ident_, key) in enumerate(
                    [(ident, "mood"), (ident, "energy"), (other, "mood")]):
                conn.execute(
                    "INSERT INTO heart_state(platform,platform_user_id,scope,state_key,"
                    "day_bucket,value,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (ident_.platform, ident_.platform_user_id, ident_.scope,
                     key, f"d{i}", json.dumps({"v": i}), now - i),
                )
        out = admin.proactive_snapshot(db)
        by_key = {item["platform_user_id"]: item for item in out["items"]}
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(set(by_key["u1"]["state"]), {"mood", "energy"})
        self.assertEqual(list(by_key["u2"]["state"]), ["mood"])
        self.assertEqual(by_key["u1"]["state"]["mood"], {"v": 0})


class CapabilitiesSnapshotTest(unittest.TestCase):
    def test_shape_and_onebot_merge(self):
        memory = mock.Mock()
        memory.settings.routes = [
            types.SimpleNamespace(name="r1", model="m1", base_url="http://x", api_key="k"),
            types.SimpleNamespace(name="r2", model="m2", base_url="http://y", api_key=""),
        ]
        memory.onebot.snapshot.return_value = {"connected": True}
        memory.settings.onebot_api = "http://api"
        memory.settings.onebot_webui = "http://ui"
        memory.settings.asr_model = "tiny"
        memory.settings.describe.return_value = {"a": 1}
        memory.presence.port = 8787
        memory.stats.return_value = {"store": {"platforms": 3}}
        out = admin.capabilities_snapshot(memory)
        self.assertEqual(out["openai_api"], True)
        self.assertTrue(out["ocr"])
        self.assertEqual(out["asr"]["model"], "tiny")
        self.assertEqual(out["platforms"], 3)
        self.assertEqual(out["onebot"]["connected"], True)
        self.assertEqual(out["onebot"]["webui"], "http://ui")
        self.assertEqual(
            out["settings"]["routes"][0],
            {"name": "r1", "model": "m1", "base_url": "http://x", "key_configured": True},
        )


if __name__ == "__main__":
    unittest.main()
