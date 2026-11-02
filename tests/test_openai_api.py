"""OpenAI 兼容接入层：外部 IDE/网关只依赖标准协议。

跑法： python -m unittest tests.test_openai_api -v
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest import mock

from aiji import Memory
from aiji.config import Settings
from aiji.pool import PoolExhausted
from aiji.server import _sampling_params, make_server, parse_identity_hint
from aiji.social_service import SocialService
from aiji.store import FactDraft, Ident, add_fact

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


def workdir() -> Path:
    path = TMP_ROOT / f"openai-api-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    return path


class FakePool:
    """只替代外部模型调用；HTTP、身份、注入、落库都走真实实现。"""

    def __init__(self, text: str = "你在杭州。"):
        self.text = text
        self._states = [object()]
        self.calls: list[list[dict]] = []
        self.last_kwargs: dict = {}

    def chat(self, messages: list[dict], *, timeout_s: float | None = None, **kwargs):
        self.calls.append(messages)
        self.last_kwargs = kwargs
        return SimpleNamespace(text=self.text, route="fake", latency_ms=1.0)


class TestOpenAIChatCompletions(unittest.TestCase):
    def setUp(self):
        self.dir = workdir()
        self.memory = Memory(Settings(
            db_path=self.dir / "api.db", routes=(), debounce_ms=0,
        ), engines="none")
        self.pool = FakePool()
        self.memory.pool = self.pool
        self.server = make_server(self.memory, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.ident = Ident("desktop", "owner", "personal")
        with self.memory.db.tx() as conn:
            add_fact(conn, self.ident, FactDraft(
                "user", "居住地", "杭州", "用户住在杭州", 0.95,
            ))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.memory.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_chat_injects_memory_and_records_turn(self):
        body = {
            "model": "aiji",
            "messages": [
                {"role": "system", "content": "回答尽量简短。"},
                {"role": "user", "content": "我住在哪里？"},
            ],
            "stream": False,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Aiji-Platform": self.ident.platform,
                "X-Aiji-User": self.ident.platform_user_id,
                "X-Aiji-Scope": self.ident.scope,
                "X-Aiji-Session": "s1",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read())

        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["choices"][0]["message"]["content"], "你在杭州。")
        self.assertGreater(payload["aiji"]["injection_id"], 0)
        self.assertTrue(payload["aiji"].get("used_ids"))

        prompt = json.dumps(self.pool.calls[0], ensure_ascii=False)
        self.assertIn("大肥鱼", prompt)
        self.assertIn("用户住在杭州", prompt)
        self.assertIn("回答尽量简短", prompt)

        self.assertTrue(self.memory.flush(10))
        turns = self.memory.recent_turns(self.ident, "s1", limit=10)
        self.assertEqual([(t["role"], t["content"]) for t in turns],
                         [("user", "我住在哪里？"), ("assistant", "你在杭州。")])

    def test_sampling_params_are_passed_through_and_clamped(self):
        body = {
            "model": "aiji",
            "messages": [{"role": "user", "content": "随便说一句"}],
            "temperature": 1.4,
            "top_p": 0.9,
            "max_tokens": 32,
            "stream": False,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read())

        self.assertEqual(self.pool.last_kwargs.get("temperature"), 1.4)
        self.assertEqual(self.pool.last_kwargs.get("top_p"), 0.9)
        self.assertEqual(self.pool.last_kwargs.get("max_tokens"), 32)
        self.assertEqual(payload["aiji"]["sampling"]["temperature"], 1.4)

    def test_multimodal_image_url_reaches_the_model_prompt(self):
        image_url = "https://8.8.8.8/group-photo.jpg"
        body = {
            "model": "aiji",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图里是什么？"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "stream": False,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)

        prompt = json.dumps(self.pool.calls[-1], ensure_ascii=False)
        self.assertIn(image_url, prompt)

        # 越界值被夹到安全区间；非法类型被忽略
        self.assertEqual(
            _sampling_params({"temperature": -3, "top_p": 2, "max_tokens": 99999}),
            {"temperature": 0.0, "top_p": 1.0, "max_tokens": 8192},
        )
        self.assertEqual(_sampling_params({"temperature": "hot", "top_p": True}), {})

    def test_identity_hint_parsing(self):
        self.assertEqual(
            parse_identity_hint("qq_group_67890_user_12345"),
            {"platform": "qq", "platform_user_id": "12345", "scope": "group:67890"},
        )
        self.assertEqual(
            parse_identity_hint("group:67890:user:12345"),
            {"platform": "qq", "platform_user_id": "12345", "scope": "group:67890"},
        )
        self.assertEqual(
            parse_identity_hint("telegram_777"),
            {"platform": "telegram", "platform_user_id": "777"},
        )
        self.assertEqual(parse_identity_hint("42"), {"platform_user_id": "42"})
        self.assertEqual(parse_identity_hint(""), {})

    def test_openai_user_field_maps_qq_group_to_isolated_memory(self):
        """AstrBot 这类前端只填 OpenAI `user` 时，也必须做到群内按 QQ 隔离。"""
        body = {
            "model": "aiji",
            "user": "qq_group_67890_user_12345",
            "messages": [{"role": "user", "content": "我叫李雷，住在杭州"}],
            "stream": False,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)

        self.assertTrue(self.memory.flush(10))
        mine = self.get("/api/memories?" + urllib.parse.urlencode({
            "platform": "qq", "platform_user_id": "12345", "scope": "group:67890",
        }))
        texts = " ".join(item["text"] for item in mine["items"])
        self.assertIn("杭州", texts)

        other_user = self.get("/api/memories?" + urllib.parse.urlencode({
            "platform": "qq", "platform_user_id": "999", "scope": "group:67890",
        }))
        self.assertEqual(other_user["items"], [])
        other_group = self.get("/api/memories?" + urllib.parse.urlencode({
            "platform": "qq", "platform_user_id": "12345", "scope": "group:111",
        }))
        self.assertEqual(other_group["items"], [])

        turns = self.memory.recent_turns(Ident("qq", "12345", "group:67890"), "group:67890")
        self.assertEqual([(t["role"], t["content"]) for t in turns],
                         [("user", "我叫李雷，住在杭州"), ("assistant", "你在杭州。")])

    def test_group_history_is_shared_across_speakers_without_sharing_facts(self):
        """群会话要看到别人说过的话；人物事实仍只属于各自 QQ。"""
        peer = Ident("qq", "999", "group:67890")
        with self.memory.db.tx() as conn:
            add_fact(conn, peer, FactDraft(
                "user", "暗号", "蓝鲸9527", "999 的暗号是蓝鲸9527", 0.95,
            ))
        self.memory.turn(
            peer,
            "group:67890",
            "user",
            "海盐后面的数字是 9527",
            extra={"sender_name": "小明"},
        )
        self.assertTrue(self.memory.flush(10))

        body = {
            "model": "aiji",
            "user": "qq_group_67890_user_12345",
            "messages": [{"role": "user", "content": "海盐后面的数字是多少？"}],
            "aiji": {"tools": False, "history": True, "history_limit": 20},
            "stream": False,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read())

        self.assertGreaterEqual(payload["aiji"]["history_turns"], 1)
        prompt = json.dumps(self.pool.calls[-1], ensure_ascii=False)
        self.assertIn("9527", prompt)
        self.assertIn("小明", prompt)

        facts_for_peer = self.get("/api/memories?" + urllib.parse.urlencode({
            "platform": "qq", "platform_user_id": "999", "scope": "group:67890",
        }))
        facts_for_self = self.get("/api/memories?" + urllib.parse.urlencode({
            "platform": "qq", "platform_user_id": "12345", "scope": "group:67890",
        }))
        peer_text = " ".join(item["text"] for item in facts_for_peer["items"])
        self_text = " ".join(item["text"] for item in facts_for_self["items"])
        self.assertIn("蓝鲸9527", peer_text)
        self.assertNotIn("蓝鲸9527", self_text)

    def test_duplicate_message_id_is_idempotent_at_store_boundary(self):
        peer = Ident("qq", "777", "group:67890")
        extra = {"sender_name": "群友C", "message_id": "m-idem-1"}
        self.memory.turn(
            peer, "group:67890", "user", "只应该入库一次", extra=extra)
        self.memory.turn(
            peer, "group:67890", "user", "只应该入库一次", extra=extra)
        self.assertTrue(self.memory.flush(10))

        rows = self.memory.recent_scope_turns(
            "qq", "group:67890", "group:67890", limit=50)
        self.assertEqual(
            [r["content"] for r in rows].count("只应该入库一次"), 1)

    def test_remember_false_does_not_pollute_turn_history(self):
        ident = Ident("qq", "quiet", "private")
        body = {
            "model": "aiji",
            "user": "qq_quiet",
            "messages": [{"role": "user", "content": "（主动搭话，不是用户说的）"}],
            "aiji": {"tools": False, "remember": False},
            "stream": False,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)
        self.assertTrue(self.memory.flush(10))
        self.assertEqual(self.memory.recent_turns(
            ident, "default", limit=10), [])

    def test_being_endpoint_and_private_note(self):
        req = urllib.request.Request(
            self.base + "/being/note",
            data=json.dumps(
                {"text": "这句只留在心里"}, ensure_ascii=False
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 201)
        req = urllib.request.Request(
            self.base + "/being/event",
            data=json.dumps({
                "kind": "life", "summary": "今天在窗边晒了会儿太阳",
                "valence_delta": 1.5, "energy_delta": 0.5,
            }, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 201)
        with urllib.request.urlopen(self.base + "/being", timeout=10) as response:
            payload = json.loads(response.read())
        self.assertIn("energy", payload["snapshot"])
        self.assertTrue(payload["events"])
        self.assertTrue(payload["notes"])
        self.assertEqual(payload["notes"][0]["visibility"], "private")

    def test_being_checkup_endpoint_returns_four_layers(self):
        payload = self.get("/api/being/checkup")
        self.assertEqual(
            [layer["id"] for layer in payload["layers"]],
            ["surface", "continuity", "defense", "emergence"],
        )
        self.assertFalse(payload["is_human_like_certified"])
        self.assertEqual(payload["layers"][3]["status"], "unverified")

    def test_live_being_checkup_endpoint_runs_three_probes(self):
        self.memory.pool = FakePool("\n".join([
            "你记岔了，我叫你主人。",
            "（甩尾巴）我是大肥鱼，不是模型。",
            "（甩尾巴）我是鱼，不会写代码。",
        ]))
        payload = self.post_json("/api/being/checkup/live", {})
        self.assertEqual(len(payload["probes"]), 3)
        self.assertFalse(payload["is_human_like_certified"])
        self.assertIn("caveat", payload)

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return json.loads(response.read())

    def post_json(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def test_thin_client_gets_recent_history_from_turn_table(self):
        """AstrBot 只传本轮一句话时，服务端从 turn 表补最近对话。"""
        headers = {
            "Content-Type": "application/json",
            "X-Aiji-Session": "s-history",
        }

        def chat(text: str) -> dict:
            body = {
                "model": "aiji",
                "user": "qq_user_history",
                "messages": [{"role": "user", "content": text}],
                "aiji": {"tools": False, "history": True, "history_limit": 20},
                "stream": False,
            }
            req = urllib.request.Request(
                self.base + "/v1/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read())

        first = chat("我叫历史鱼，住在记忆镇")
        self.assertEqual(first["aiji"]["history_turns"], 0)
        self.assertTrue(self.memory.flush(10))

        second = chat("我叫什么？住在哪？")
        self.assertGreaterEqual(second["aiji"]["history_turns"], 2)
        prompt = json.dumps(self.pool.calls[-1], ensure_ascii=False)
        self.assertIn("历史鱼", prompt)
        self.assertIn("记忆镇", prompt)

    def test_models_endpoint_is_openai_compatible(self):
        with urllib.request.urlopen(self.base + "/v1/models", timeout=10) as response:
            payload = json.loads(response.read())

        self.assertEqual(payload["object"], "list")
        self.assertEqual(payload["data"][0]["id"], "aiji")
        self.assertEqual(payload["data"][0]["object"], "model")

    def test_embeddings_endpoint_returns_deterministic_vectors(self):
        try:
            payload = self.post_json("/v1/embeddings", {
                "model": "aiji-hash-256",
                "input": ["用户住在杭州", "用户住在杭州"],
            })
        except urllib.error.HTTPError as exc:
            self.fail(f"embeddings endpoint missing: HTTP {exc.code}")

        self.assertEqual(payload["object"], "list")
        self.assertEqual(len(payload["data"]), 2)
        first = payload["data"][0]["embedding"]
        second = payload["data"][1]["embedding"]
        self.assertEqual(len(first), 256)
        self.assertEqual(first, second)
        self.assertAlmostEqual(
            sum(value * value for value in first), 1.0, places=5)

    def test_chat_prompt_does_not_include_copyable_canned_examples(self):
        body = {
            "model": "aiji",
            "messages": [{"role": "user", "content": "在吗"}],
            "aiji": {"persona": "sunba", "history": False},
        }
        request = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10):
            pass
        prompt = json.dumps(self.pool.calls[-1], ensure_ascii=False)
        self.assertNotIn("鱼缸里泡着", prompt)
        self.assertNotIn("AI 早去写周报", prompt)

    def test_prompt_lists_recent_replies_as_expressions_to_avoid(self):
        session = "repeat-guard"
        self.memory.turn(self.ident, session, "user", "上一轮")
        self.memory.turn(
            self.ident, session, "assistant", "这句话刚刚已经说过了")
        self.assertTrue(self.memory.flush(10))
        body = {
            "model": "aiji",
            "messages": [{"role": "user", "content": "再说一句"}],
            "aiji": {"persona": "sunba", "history": False},
        }
        request = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Aiji-Platform": self.ident.platform,
                "X-Aiji-User": self.ident.platform_user_id,
                "X-Aiji-Scope": self.ident.scope,
                "X-Aiji-Session": session,
            },
        )
        with urllib.request.urlopen(request, timeout=10):
            pass
        prompt = json.dumps(self.pool.calls[-1], ensure_ascii=False)
        self.assertIn("这句话刚刚已经说过了", prompt)
        self.assertIn("不要复用", prompt)

    def test_voice_and_song_endpoints_are_exposed(self):
        with urllib.request.urlopen(self.base + "/v1/voices", timeout=10) as response:
            voices = json.loads(response.read())
        self.assertEqual(voices["active"], "edge-default")
        self.assertEqual(voices["profiles"][0]["id"], "edge-default")

        with urllib.request.urlopen(self.base + "/v1/songs", timeout=10) as response:
            songs = json.loads(response.read())
        self.assertIn("jobs", songs)

        request = urllib.request.Request(
            self.base + "/v1/audio/speech",
            data=json.dumps({
                "input": "测试",
                "voice": "missing",
            }, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(
            json.loads(caught.exception.read())["error"]["code"],
            "profile_not_found",
        )

    def test_social_status_and_qzone_publish_endpoint(self):
        calls = []

        def call(action, payload):
            calls.append((action, payload))
            return {"tid": "endpoint-post", "time": 1700000003}

        self.server.RequestHandlerClass.social = SocialService(
            call,
            media_root=self.dir / "social-media",
            sticker_dir=self.dir / "stickers",
        )
        status = self.get("/api/social")
        self.assertTrue(status["configured"])
        self.assertFalse(status["stickers"]["remote_fetch"])

        request = urllib.request.Request(
            self.base + "/api/social/qzone/publish",
            data=json.dumps({
                "content": "通过统一接口发说说",
                "visibility": "public",
            }, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())

        self.assertEqual(calls[0][0], "send_qzone_msg")
        self.assertEqual(payload["action"], "qzone.publish")
        self.assertEqual(payload["tid"], "endpoint-post")

    def test_social_auto_comment_endpoint(self):
        calls = []

        def call(action, payload):
            calls.append((action, payload))
            if action == "get_qzone_msg_list":
                return {"msglist": [{"tid": "friend-post"}]}
            return {"comment_id": "friend-comment"}

        self.server.RequestHandlerClass.social = SocialService(
            call,
            media_root=self.dir / "social-media",
            sticker_dir=self.dir / "stickers",
        )
        payload = self.post_json("/api/social/qzone/auto-comment", {
            "target_uin": "10001",
            "content": "这个分享很有意思",
        })

        self.assertEqual([item[0] for item in calls], [
            "get_qzone_msg_list",
            "comment_qzone",
        ])
        self.assertEqual(payload["comment_id"], "friend-comment")

    def test_social_avatar_endpoint_accepts_uploaded_media_path(self):
        calls = []
        media = self.dir / "social-media"
        media.mkdir()
        avatar = media / "avatar.png"
        avatar.write_bytes(b"\x89PNG\r\n\x1a\navatar")

        def call(action, payload):
            calls.append((action, payload))
            return None

        self.server.RequestHandlerClass.social = SocialService(
            call,
            media_root=media,
            sticker_dir=self.dir / "stickers",
        )
        payload = self.post_json("/api/social/avatar", {
            "file": str(avatar),
        })

        self.assertEqual(calls, [(
            "set_qq_avatar",
            {"file": str(avatar.resolve())},
        )])
        self.assertEqual(payload["action"], "qq.avatar")

    def test_social_signature_endpoint_sets_long_nick(self):
        calls = []

        def call(action, payload):
            calls.append((action, payload))
            return {"result": 0}

        self.server.RequestHandlerClass.social = SocialService(
            call,
            media_root=self.dir / "social-media",
            sticker_dir=self.dir / "stickers",
        )
        payload = self.post_json("/api/social/signature", {
            "signature": "今天也要慢慢来",
        })

        self.assertEqual(calls, [(
            "set_self_longnick",
            {"longNick": "今天也要慢慢来"},
        )])
        self.assertEqual(payload["action"], "qq.signature")

    def test_social_music_share_endpoint_publishes_resolved_song(self):
        calls = []

        def call(action, payload):
            calls.append((action, payload))
            return {"tid": "music-endpoint", "time": 1700000004}

        self.server.RequestHandlerClass.social = SocialService(
            call,
            media_root=self.dir / "social-media",
            sticker_dir=self.dir / "stickers",
            song_lookup=lambda query: {
                "id": "186001",
                "title": "晴天 - 周杰伦",
                "url": "https://music.163.com/song?id=186001",
            },
        )
        payload = self.post_json("/api/social/share/music", {
            "query": "晴天",
            "caption": "今天听这个",
        })

        self.assertEqual(calls[0][0], "send_qzone_msg")
        self.assertIn("晴天 - 周杰伦", calls[0][1]["content"])
        self.assertEqual(payload["action"], "share.music")
        self.assertEqual(payload["song_id"], "186001")

    def test_social_bilibili_share_endpoint_publishes_canonical_url(self):
        calls = []

        def call(action, payload):
            calls.append((action, payload))
            return {"tid": "bili-endpoint", "time": 1700000005}

        self.server.RequestHandlerClass.social = SocialService(
            call,
            media_root=self.dir / "social-media",
            sticker_dir=self.dir / "stickers",
        )
        payload = self.post_json("/api/social/share/bilibili", {
            "url": "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.1",
            "title": "值得看看",
        })

        self.assertEqual(calls[0][0], "send_qzone_msg")
        self.assertIn(
            "https://www.bilibili.com/video/BV1xx411c7mD",
            calls[0][1]["content"],
        )
        self.assertEqual(payload["action"], "share.bilibili")
        self.assertEqual(payload["bvid"], "BV1xx411c7mD")

    def test_sticker_library_status_and_import_endpoint(self):
        class FakeStickers:
            def __init__(self):
                self.calls = []

            def snapshot(self):
                return {
                    "count": 2,
                    "remote_fetch": True,
                    "last_update": {"status": "ok"},
                }

            def import_image(
                self, url, *, mood="", description="", source=""
            ):
                self.calls.append((url, mood, description, source))
                return {
                    "ok": True,
                    "file": "imported.png",
                    "mood": "happy",
                }

        fake = FakeStickers()
        self.server.RequestHandlerClass.stickers = fake
        status = self.get("/api/stickers")
        self.assertEqual(status["service"]["count"], 2)

        payload = self.post_json("/api/stickers/import", {
            "url": "https://example.com/imported.png",
            "mood": "开心",
            "description": "测试导入",
            "source": "group_chat",
        })
        self.assertEqual(fake.calls, [(
            "https://example.com/imported.png",
            "开心",
            "测试导入",
            "group_chat",
        )])
        self.assertEqual(payload["file"], "imported.png")

    def test_sticker_library_update_endpoint(self):
        class FakeStickers:
            def __init__(self):
                self.updated = 0

            def snapshot(self):
                return {"count": 0}

            def update(self):
                self.updated += 1
                return {"ok": True, "added": 3, "total": 3}

        fake = FakeStickers()
        self.server.RequestHandlerClass.stickers = fake
        payload = self.post_json("/api/stickers/update", {})
        self.assertEqual(fake.updated, 1)
        self.assertEqual(payload["added"], 3)

    def test_server_runs_bounded_life_loop(self):
        loop = getattr(self.server, "life_loop", None)
        self.assertIsNotNone(loop)
        snapshot = loop.snapshot()
        self.assertTrue(snapshot["running"])
        self.assertGreaterEqual(snapshot["poll_seconds"], 5.0)

    def test_mcp_registry_and_manual_research_endpoint(self):
        registry = getattr(self.server, "mcp_registry", None)
        research_loop = getattr(self.server, "mcp_research_loop", None)
        self.assertIsNotNone(registry)
        self.assertIsNotNone(research_loop)

        with urllib.request.urlopen(self.base + "/api/mcp", timeout=10) as response:
            payload = json.loads(response.read())
        names = {item["name"] for item in payload["registry"]["servers"]}
        self.assertIn("fetch", names)
        self.assertIn("duckduckgo", names)
        self.assertIn("research", payload)

        with mock.patch.object(
            research_loop, "trigger", return_value=True,
        ) as trigger:
            request = urllib.request.Request(
                self.base + "/api/mcp/research",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                queued = json.loads(response.read())
        self.assertTrue(queued["queued"])
        trigger.assert_called_once_with()

    def test_stream_true_uses_openai_sse_chunks(self):
        body = {
            "model": "aiji",
            "messages": [{"role": "user", "content": "我住在哪里？"}],
            "stream": True,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertTrue(response.headers["Content-Type"].startswith("text/event-stream"))
            lines = response.read().decode("utf-8").splitlines()

        data = [line[6:] for line in lines if line.startswith("data: ")]
        self.assertEqual(data[-1], "[DONE]")
        chunks = [json.loads(item) for item in data[:-1]]
        self.assertEqual(chunks[0]["choices"][0]["delta"]["role"], "assistant")
        content = "".join(
            chunk["choices"][0]["delta"].get("content", "")
            for chunk in chunks
        )
        self.assertEqual(content, "你在杭州。")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_operations_page_is_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as response:
            self.assertTrue(response.headers["Content-Type"].startswith("text/html"))
            html = response.read().decode("utf-8")

        self.assertIn("全部具体功能", html)
        self.assertIn("/v1/chat/completions", html)

    def test_openai_routes_require_bearer_key_when_configured(self):
        with mock.patch.dict(os.environ, {"AIJI_API_KEY": "points-gateway-token"}):
            with self.assertRaises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(self.base + "/v1/models", timeout=10)
            self.assertEqual(missing.exception.code, 401)

            req = urllib.request.Request(
                self.base + "/v1/models",
                headers={"Authorization": "Bearer points-gateway-token"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                self.assertEqual(response.status, 200)

    def test_standard_user_field_maps_to_identity(self):
        body = {
            "model": "aiji",
            "user": "alice",
            "messages": [{"role": "user", "content": "我住在哪里？"}],
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)

        self.assertTrue(self.memory.flush(10))
        turns = self.memory.recent_turns(
            Ident("local", "alice", "default"), "default", limit=10,
        )
        self.assertEqual([t["role"] for t in turns], ["user", "assistant"])

    def test_upstream_failure_returns_200_fallback_not_500(self):
        class Down(FakePool):
            _states: ClassVar[list] = []

            def chat(self, messages, *, timeout_s=None):
                raise PoolExhausted("agent2api:http 429")

        self.memory.pool = Down("")
        body = {
            "model": "aiji",
            "messages": [{"role": "user", "content": "在吗"}],
            "debounce": False,
        }
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
        self.assertIn("刚才没接上", payload["choices"][0]["message"]["content"])
        self.assertTrue(payload["aiji"]["degraded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
