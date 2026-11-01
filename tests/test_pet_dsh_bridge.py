"""DSH 桥接的纯逻辑测试（不真的起 agent，避免慢与花钱）。"""

from __future__ import annotations

import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from aiji.pet.dsh_bridge import DshBridge, load_credential_refs

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


@contextmanager
def tmpdir(prefix: str = "bridge"):
    """沙箱下 `tempfile.TemporaryDirectory()` 建 0o700 目录会写不进去；统一落 .tmp。"""
    path = TMP_ROOT / f"{prefix}-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class TestCredentialRefs(unittest.TestCase):
    def test_parse_refs_section(self):
        with tmpdir() as d:
            p = Path(d) / "cred.yaml"
            p.write_text(
                "version: 1\n"
                "refs:\n"
                "  WB_PROXY_API_KEY: abc123\n"
                "  AGENT2API_KEY: 'sk-xyz'\n"
                "records:\n"
                "  - kind: token\n"
                "    DEEPSEEK_API_KEY: should_not_appear\n",
                encoding="utf-8")
            refs = load_credential_refs(p)
            self.assertEqual(refs.get("WB_PROXY_API_KEY"), "abc123")
            self.assertEqual(refs.get("AGENT2API_KEY"), "sk-xyz")
            self.assertNotIn("DEEPSEEK_API_KEY", refs)

    def test_missing_file_is_empty(self):
        self.assertEqual(load_credential_refs("Z:/definitely/missing.yaml"), {})

    def test_ignores_comments_and_blanks(self):
        with tmpdir() as d:
            p = Path(d) / "c.yaml"
            p.write_text("refs:\n  # note\n\n  KEY_A: v1\n", encoding="utf-8")
            self.assertEqual(load_credential_refs(p), {"KEY_A": "v1"})


class TestBridgeCommand(unittest.TestCase):
    def test_cmd_shape(self):
        b = DshBridge(cwd=".", timeout_s=5)
        cmd = b._cmd("你好")
        self.assertEqual(cmd[1:3], ["headless", "--json"])
        self.assertEqual(cmd[-1], "你好")
        self.assertNotIn("--session-id", cmd)

    def test_cmd_with_session(self):
        b = DshBridge(cwd=".", timeout_s=5, session_id="session-abc")
        cmd = b._cmd("继续")
        self.assertIn("--session-id", cmd)
        self.assertEqual(cmd[cmd.index("--session-id") + 1], "session-abc")

    def test_env_injects_credentials_without_overriding_real_env(self):
        with tmpdir() as d:
            p = Path(d) / "cred.yaml"
            p.write_text("refs:\n  PATH: nope\n  SOME_KEY: injected\n", encoding="utf-8")
            b = DshBridge(cwd=".", credentials=p)
            env = b._env()
            self.assertEqual(env.get("SOME_KEY"), "injected")
            self.assertNotEqual(env.get("PATH"), "nope")  # 已有环境变量不被覆盖

    def test_empty_text_short_circuits(self):
        b = DshBridge(cwd=".")
        r = b.ask("   ")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "empty")
        self.assertEqual(r["took_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
