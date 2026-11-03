"""MCP 客户端：配置校验（安全边界）、工具白名单、只读门槛、stdio 会话。

这个模块是**把外部进程拉起来**的唯一入口，安全约束全在配置校验里：
命令必须命中白名单、禁止 shell 元字符、`allowed_tools` 必须显式列出、
`read_only=false` 一律拒绝。任何一条漏了，等于把任意命令执行权交给配置文件。
所以每条拒绝都要有用例，且要断言**具体是哪条拒绝**。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.left_hand import mcp_client as mc  # noqa: E402


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "mcp"))

    def write(self, payload) -> pathlib.Path:
        path = self.dir / "mcp.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def registry(self, payload, **kwargs):
        return mc.MCPRegistry(self.write(payload), **kwargs)

    def server(self, **overrides) -> dict:
        base = {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "allowed_tools": ["fetch"],
            "enabled": True,
        }
        base.update(overrides)
        return base


class ConfigValidationTest(ConfigCase):
    def test_readable_config(self):
        reg = self.registry({"servers": {"fetch": self.server(
            env={"TOKEN": "$HOME/x"}, timeout_s=10, max_output_chars=500,
            read_only=True, note="取网页")}})
        cfg = reg.get("fetch")
        self.assertEqual(cfg.command, "uvx")
        self.assertEqual(cfg.allowed_tools, frozenset({"fetch"}))
        self.assertEqual(cfg.timeout_s, 10.0)
        self.assertEqual(cfg.max_output_chars, 1000, "下限夹到 1000")
        self.assertEqual(cfg.note, "取网页")

    def test_missing_or_broken_file(self):
        reg_path = self.dir / "nope.json"
        with self.assertRaises(mc.MCPConfigError) as ctx:
            mc.MCPRegistry(reg_path)
        self.assertIn("cannot read MCP config", str(ctx.exception))

        bad = self.dir / "bad.json"
        bad.write_text("{不是 json", encoding="utf-8")
        with self.assertRaises(mc.MCPConfigError):
            mc.MCPRegistry(bad)

    def test_servers_must_be_an_object(self):
        with self.assertRaises(mc.MCPConfigError) as ctx:
            self.registry({"servers": ["fetch"]})
        self.assertIn("servers object", str(ctx.exception))

    def test_non_dict_server_entries_are_skipped(self):
        reg = self.registry({"servers": {"坏条目": "uvx", "fetch": self.server()}})
        self.assertEqual([cfg.name for cfg in reg._servers.values()], ["fetch"])

    def test_names_are_sanitized_and_empty_names_dropped(self):
        reg = self.registry({"servers": {
            "weird name!!!": self.server(),
            "***": self.server(),
        }})
        self.assertEqual(sorted(reg._servers), ["weird-name"])

    def test_command_is_required(self):
        with self.assertRaises(mc.MCPConfigError) as ctx:
            self.registry({"servers": {"fetch": self.server(command="  ")}})
        self.assertIn("command is required", str(ctx.exception))

    def test_shell_metacharacters_refused_in_command_and_args(self):
        for command in ("uvx; rm -rf /", "uvx && whoami", "uvx | tee x"):
            with self.assertRaises(mc.MCPConfigError) as ctx:
                self.registry({"servers": {"x": self.server(command=command)}})
            self.assertIn("unsafe command", str(ctx.exception))
        with self.assertRaises(mc.MCPConfigError) as ctx:
            self.registry({"servers": {"x": self.server(args=["a", "b`whoami`"])}})
        self.assertIn("invalid args", str(ctx.exception))

    def test_enabled_command_must_be_allowlisted(self):
        with self.assertRaises(mc.MCPConfigError) as ctx:
            self.registry({"servers": {"x": self.server(command="powershell")}})
        self.assertIn("not allowlisted", str(ctx.exception))

    def test_disabled_server_may_use_other_commands(self):
        reg = self.registry({"servers": {"x": self.server(
            command="docker", enabled=False)}})
        self.assertEqual(reg._servers["x"].command, "docker")
        with self.assertRaises(mc.MCPConfigError) as ctx:
            reg.get("x")
        self.assertIn("disabled", str(ctx.exception))

    def test_args_env_and_tools_shape(self):
        bad_args = [{"command": "uvx", "args": "mcp-server-fetch"},
                    {"command": "uvx", "args": ["ok", 1]}]
        for payload in bad_args:
            with self.assertRaises(mc.MCPConfigError) as ctx:
                self.registry({"servers": {"x": self.server(**payload)}})
            self.assertIn("invalid args", str(ctx.exception))

        for payload in ({"env": {"K": 1}}, {"env": ["K=V"]}):
            with self.assertRaises(mc.MCPConfigError) as ctx:
                self.registry({"servers": {"x": self.server(**payload)}})
            self.assertIn("invalid env", str(ctx.exception))

        for payload in ({"allowed_tools": ["  "]}, {"allowed_tools": "fetch"}):
            with self.assertRaises(mc.MCPConfigError) as ctx:
                self.registry({"servers": {"x": self.server(**payload)}})
            self.assertIn("allowed_tools", str(ctx.exception))

        # 空列表是允许的：它不是"没限制"，而是"什么都不许"（默认拒绝）。
        # 这条行为故意钉住——别哪天被改成"空等于全放行"。
        empty = self.registry({"servers": {"x": self.server(allowed_tools=[])}})
        self.assertEqual(empty.get("x").allowed_tools, frozenset())
        with self.assertRaises(mc.MCPToolDenied):
            empty.call_sync("x", "fetch", {})

    def test_timeout_and_output_are_clamped(self):
        reg = self.registry({"servers": {"x": self.server(
            timeout_s=1, max_output_chars=10_000_000)}})
        cfg = reg.get("x")
        self.assertEqual(cfg.timeout_s, 5.0)
        self.assertEqual(cfg.max_output_chars, 100_000)

    def test_env_vars_are_expanded(self):
        with mock.patch.dict(mc.os.environ, {"AIJI_TEST_TOKEN": "s3cret"}):
            reg = self.registry({"servers": {"x": self.server(
                env={"TOKEN": "$AIJI_TEST_TOKEN", "LITERAL": "abc"})}})
        self.assertEqual(reg.get("x").env,
                         {"TOKEN": "s3cret", "LITERAL": "abc"})

    def test_get_unknown_server(self):
        reg = self.registry({"servers": {}})
        with self.assertRaises(mc.MCPConfigError) as ctx:
            reg.get("没有这个")
        self.assertIn("not found", str(ctx.exception))

    def test_snapshot_lists_servers_and_cache(self):
        reg = self.registry({"servers": {"x": self.server(note="备注")}})
        reg._tool_cache["x"] = [{"name": "fetch"}]
        snap = reg.snapshot()
        self.assertEqual(len(snap["servers"]), 1)
        entry = snap["servers"][0]
        self.assertEqual(entry["name"], "x")
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["tools"], ["fetch"])
        self.assertEqual(entry["cached_tools"], [{"name": "fetch"}])


class GatingTest(ConfigCase):
    def setUp(self):
        super().setUp()
        self.reg = self.registry({"servers": {"x": self.server(
            allowed_tools=["fetch", "search"])}})

    def test_tool_must_be_allowlisted(self):
        with self.assertRaises(mc.MCPToolDenied) as ctx:
            self.reg.call_sync("x", "exec_shell", {})
        self.assertIn("not allowed", str(ctx.exception))

    def test_non_read_only_server_is_refused(self):
        reg = self.registry({"servers": {"x": self.server(read_only=False)}})
        with self.assertRaises(mc.MCPToolDenied) as ctx:
            reg.call_sync("x", "fetch", {})
        self.assertIn("not marked read-only", str(ctx.exception))

    def test_busy_server_is_refused_without_blocking(self):
        """锁被占时要立刻报 busy —— 用假锁，别真等 5 秒超时（那会把 suite 拖慢 5 s）。"""
        class HeldLock:
            def __init__(self):
                self.timeout = None

            def acquire(self, timeout=None):
                self.timeout = timeout
                return False

            def release(self):
                raise AssertionError("没拿到锁就不该 release")

        held = HeldLock()
        self.reg._locks["x"] = held
        with self.assertRaises(mc.MCPError) as ctx:
            self.reg.call_sync("x", "fetch", {})
        self.assertIn("busy", str(ctx.exception))
        self.assertEqual(held.timeout, 5)

    def test_run_sync_passes_mcp_errors_through(self):
        async def boom():
            raise mc.MCPToolDenied("明确拒绝")

        with self.assertRaises(mc.MCPToolDenied):
            self.reg._run_sync(boom())

    def test_run_sync_wraps_other_exceptions(self):
        async def boom():
            raise ValueError("连接断了")

        with self.assertRaises(mc.MCPError) as ctx:
            self.reg._run_sync(boom())
        self.assertIn("ValueError: 连接断了", str(ctx.exception))

    def test_error_text_flattens_groups(self):
        group = ExceptionGroup("组合", [ValueError("甲"), KeyError("乙")])
        self.assertEqual(mc._error_text(group), "ValueError: 甲; KeyError: '乙'")
        self.assertEqual(mc._error_text(ValueError("单个")), "ValueError: 单个")


class TimeoutTest(ConfigCase):
    def test_timeout_becomes_mcp_error(self):
        async def scenario():
            async def slow():
                await asyncio.sleep(5)
            return await mc.MCPRegistry._with_timeout(slow(), 1.0)

        with self.assertRaises(mc.MCPError) as ctx:
            asyncio.run(scenario())
        self.assertIn("timed out", str(ctx.exception))

    def test_timeout_floor_is_one_second(self):
        async def scenario():
            async def quick():
                return "ok"
            return await mc.MCPRegistry._with_timeout(quick(), 0.01)

        self.assertEqual(asyncio.run(scenario()), "ok")


class FakeSession:
    """假 mcp 包：stdio_client 是异步上下文管理器，ClientSession 记录调用。"""

    def __init__(self, tools, content, is_error=False):
        self.tools = tools
        self.content = content
        self.is_error = is_error
        self.calls: list[tuple[str, tuple]] = []

    def install(self):
        registry = self

        class _CM:
            def __init__(self, value):
                self.value = value

            async def __aenter__(self):
                return self.value

            async def __aexit__(self, *_exc):
                return False

        class Tool:
            def __init__(self, name, description="", schema=None):
                self.name = name
                self.description = description
                self.input_schema = schema

        class Result:
            def __init__(self):
                self.tools = registry.tools

        class CallResult:
            def __init__(self):
                self.content = registry.content
                self.is_error = registry.is_error

        class Session:
            def __init__(self, _read, _write):
                pass

            async def initialize(self):
                registry.calls.append(("initialize", ()))

            async def list_tools(self):
                registry.calls.append(("list_tools", ()))
                return Result()

            async def call_tool(self, tool, arguments):
                registry.calls.append(("call_tool", (tool, arguments)))
                return CallResult()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        module = types.ModuleType("mcp")
        module.ClientSession = Session
        module.StdioServerParameters = lambda **kwargs: kwargs
        client = types.ModuleType("mcp.client")
        client.__path__ = []
        stdio = types.ModuleType("mcp.client.stdio")
        stdio.stdio_client = lambda params: _CM(("读", "写"))
        module.client = client
        client.stdio = stdio
        return {
            "mcp": module,
            "mcp.client": client,
            "mcp.client.stdio": stdio,
            "_Tool": Tool,
        }, module


class SessionTest(ConfigCase):
    def reg(self, **overrides):
        return self.registry({"servers": {"x": self.server(**overrides)}})

    def test_list_tools_filters_by_allowlist(self):
        fake = FakeSession(tools=[], content=[])
        modules, module = fake.install()
        fake.tools = [
            module_tool("fetch", "取网页", {"type": "object"}),
            module_tool("exec", "跑命令", None),
        ]
        with mock.patch.dict(sys.modules, modules):
            tools = self.reg(allowed_tools=["fetch"]).list_tools_sync("x")
        self.assertEqual([t["name"] for t in tools], ["fetch"])
        self.assertEqual(tools[0]["description"], "取网页")
        self.assertEqual(tools[0]["input_schema"], {"type": "object"})
        self.assertEqual([c[0] for c in fake.calls], ["initialize", "list_tools"])

    def test_call_tool_joins_text_content_and_truncates(self):
        fake = FakeSession(tools=[], content=[
            types.SimpleNamespace(text="第一段"),
            types.SimpleNamespace(image="二进制"),
            types.SimpleNamespace(text="第二段"),
        ])
        modules, _module = fake.install()
        with mock.patch.dict(sys.modules, modules):
            result = self.reg(max_output_chars=1000).call_sync(
                "x", "fetch", {"url": "https://e.com"})
        self.assertEqual(result.text, "第一段\n[SimpleNamespace]\n第二段")
        self.assertEqual(result.server, "x")
        self.assertEqual(result.tool, "fetch")
        self.assertFalse(result.is_error)
        self.assertGreaterEqual(result.took_ms, 0.0)
        self.assertIn(("call_tool", ("fetch", {"url": "https://e.com"})), fake.calls)

    def test_long_output_is_truncated_with_marker(self):
        fake = FakeSession(tools=[], content=[
            types.SimpleNamespace(text="字" * 5000)])
        modules, _module = fake.install()
        with mock.patch.dict(sys.modules, modules):
            result = self.reg(max_output_chars=1000).call_sync("x", "fetch", {})
        self.assertTrue(result.text.endswith("...[truncated]"))
        self.assertEqual(len(result.text), 1000 + len("\n...[truncated]"))

    def test_is_error_is_surfaced(self):
        fake = FakeSession(tools=[], content=[types.SimpleNamespace(text="失败")],
                           is_error=True)
        modules, _module = fake.install()
        with mock.patch.dict(sys.modules, modules):
            result = self.reg().call_sync("x", "fetch", {})
        self.assertTrue(result.is_error)

    def test_probe_caches_tools(self):
        fake = FakeSession(tools=[module_tool("fetch", "取网页", {})], content=[])
        modules, _module = fake.install()
        reg = self.reg()
        with mock.patch.dict(sys.modules, modules):
            out = reg.probe_sync("x")
        self.assertEqual(out["server"], "x")
        self.assertEqual([t["name"] for t in out["tools"]], ["fetch"])
        self.assertEqual(reg._tool_cache["x"], out["tools"])
        self.assertEqual(reg.snapshot()["servers"][0]["cached_tools"], out["tools"])

    def test_missing_dependency_is_reported(self):
        reg = self.reg()
        with mock.patch.dict(sys.modules, {"mcp": None}):
            with self.assertRaises(mc.MCPError) as ctx:
                reg.list_tools_sync("x")
        self.assertIn("MCP dependency is missing", str(ctx.exception))

    def test_env_and_cwd_are_passed_to_the_child(self):
        seen = {}
        fake = FakeSession(tools=[], content=[])

        def params(**kwargs):
            seen.update(kwargs)
            return kwargs

        modules, module = fake.install()
        module.StdioServerParameters = params
        with mock.patch.dict(sys.modules, modules):
            self.reg(env={"TOKEN": "t"}, cwd="D:/x").list_tools_sync("x")
        self.assertEqual(seen["env"]["TOKEN"], "t")
        self.assertIn("PATH", seen["env"], "子进程要继承当前环境")
        self.assertEqual(seen["cwd"], "D:/x")

    def test_no_env_means_inherit_only(self):
        seen = {}
        fake = FakeSession(tools=[], content=[])

        modules, module = fake.install()
        module.StdioServerParameters = lambda **kwargs: seen.update(kwargs) or kwargs
        with mock.patch.dict(sys.modules, modules):
            self.reg().list_tools_sync("x")
        self.assertIsNone(seen["env"], "没配 env 就传 None（完全继承）")


def module_tool(name: str, description: str, schema) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, description=description,
                                 input_schema=schema)


if __name__ == "__main__":
    unittest.main()
