"""MCP client, discovery and research-loop regressions."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiji.left_hand.mcp_client import MCPRegistry, MCPToolDenied
from aiji.left_hand.mcp_research import MCPResearchLoop, MCPResearcher, classify_readme

HAS_MCP = importlib.util.find_spec("mcp") is not None


class FakeGitHub:
    def search_repositories(self, query: str, limit: int = 20):
        return [
            {
                "full_name": "safe/readonly-mcp",
                "html_url": "https://github.com/safe/readonly-mcp",
                "default_branch": "main",
                "description": "Read-only MCP server for search and fetch",
                "stargazers_count": 120,
                "updated_at": "2026-10-20T00:00:00Z",
                "archived": False,
                "license": {"spdx_id": "MIT"},
                "topics": ["mcp-server", "search"],
            },
            {
                "full_name": "danger/write-mcp",
                "html_url": "https://github.com/danger/write-mcp",
                "default_branch": "main",
                "description": "MCP server with terminal and file deletion",
                "stargazers_count": 90,
                "updated_at": "2026-10-20T00:00:00Z",
                "archived": False,
                "license": {"spdx_id": "MIT"},
                "topics": ["mcp-server"],
            },
        ]


def fake_fetch(url: str) -> str:
    if "safe/readonly-mcp" in url:
        return (
            "# Read-only MCP\n\n"
            "Tools: search repositories, fetch pages and read documentation.\n"
            "Run: uvx readonly-mcp\n"
        )
    return (
        "# Dangerous MCP\n\n"
        "Tools: execute terminal commands, delete files and deploy servers.\n"
    )


class TestMCPClient(unittest.TestCase):
    @unittest.skipUnless(HAS_MCP, "mcp dependency is not installed")
    def test_stdio_client_lists_and_calls_allowlisted_tool(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps({
                "servers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(root / "tests" / "fixtures" / "fake_mcp_server.py")],
                        "enabled": True,
                        "allowed_tools": ["echo"],
                        "timeout_s": 20,
                    },
                },
            }), encoding="utf-8")
            registry = MCPRegistry(config)
            result = registry.call_sync(
                "fake", "echo", {"text": "hello"}, timeout_s=20)
            self.assertFalse(result.is_error)
            self.assertEqual(result.text, "echo:hello")
            self.assertEqual(
                [tool["name"] for tool in registry.probe_sync("fake")["tools"]],
                ["echo"],
            )

    def test_unlisted_tool_is_rejected_before_process_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps({
                "servers": {
                    "safe": {
                        "command": "uvx",
                        "args": ["mcp-server-fetch"],
                        "enabled": True,
                        "allowed_tools": ["fetch"],
                    },
                },
            }), encoding="utf-8")
            registry = MCPRegistry(config)
            with self.assertRaises(MCPToolDenied):
                registry.call_sync("safe", "write_file", {})

    def test_disabled_server_can_document_a_future_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps({
                "servers": {
                    "future-github": {
                        "command": "github-mcp-server",
                        "args": ["stdio"],
                        "enabled": False,
                        "allowed_tools": ["search_repositories"],
                    },
                },
            }), encoding="utf-8")
            registry = MCPRegistry(config)
            server = next(
                item for item in registry.snapshot()["servers"]
                if item["name"] == "future-github"
            )
            self.assertFalse(server["enabled"])


class TestMCPResearch(unittest.TestCase):
    def test_research_delay_uses_persisted_report_not_boot_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            researcher = MCPResearcher(output_dir=tmp, github=FakeGitHub(), fetch_text=fake_fetch)
            loop = MCPResearchLoop(researcher, interval_s=3600)
            self.assertEqual(loop.due_delay(now=10000), 0)
            (Path(tmp) / "latest.json").write_text(
                json.dumps({"created_at": 9000}), encoding="utf-8")
            self.assertEqual(loop.due_delay(now=10000), 2600)
            self.assertEqual(loop.due_delay(now=13000), 0)

    def test_readme_risk_classifier_blocks_write_and_shell_tools(self):
        safe = classify_readme(
            "Read-only MCP server. Search and fetch web pages.")
        risky = classify_readme(
            "MCP server to execute shell commands, delete files and publish posts.")
        self.assertEqual(safe["risk"], "read_only")
        self.assertEqual(risky["risk"], "blocked")

    def test_mixed_read_and_write_capabilities_require_review(self):
        result = classify_readme(
            "MCP server to search and read repositories; "
            "it can also create issues and update labels.")
        self.assertEqual(result["risk"], "review_required")

    def test_install_config_words_do_not_override_read_only_tool_list(self):
        readme = """
        Create this config and run the uvx command.
        ## Available Tools
        - search: search the web
        - fetch_content: fetch page text
        Trusted local deployments may fetch internal hosts.
        ## Deployment
        Run the deploy command later.
        """
        result = classify_readme(readme)
        self.assertEqual(result["risk"], "read_only")

    def test_research_saves_candidates_without_executing_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            events: list[str] = []
            researcher = MCPResearcher(
                output_dir=tmp,
                github=FakeGitHub(),
                fetch_text=fake_fetch,
                observer=events.append,
                max_candidates=5,
            )
            report = researcher.run_once(now=1790000000.0)

            self.assertEqual(report["discovered"], 2)
            self.assertEqual(report["eligible"], 1)
            latest = json.loads(
                (Path(tmp) / "latest.json").read_text("utf-8"))
            self.assertIn("summary", latest)
            safe = next(
                item for item in latest["candidates"]
                if item["full_name"] == "safe/readonly-mcp"
            )
            danger = next(
                item for item in latest["candidates"]
                if item["full_name"] == "danger/write-mcp"
            )
            self.assertTrue(safe["eligible"])
            self.assertEqual(safe["risk"], "read_only")
            self.assertEqual(danger["risk"], "blocked")
            self.assertFalse(danger["auto_install_allowed"])
            self.assertEqual(len(events), 1)
            self.assertIn("MCP", events[0])

    def test_pre_score_prefers_read_only_server_over_browser_automation(self):
        safe = {
            "full_name": "safe/search-server",
            "description": "Read-only MCP server for search and fetch",
            "topics": ["mcp-server", "search"],
            "stargazers_count": 100,
        }
        risky = {
            "full_name": "risky/browser-server",
            "description": "MCP server for browser automation and write actions",
            "topics": ["mcp-server", "browser-automation"],
            "stargazers_count": 50_000,
        }
        self.assertGreater(
            MCPResearcher._pre_score(safe),
            MCPResearcher._pre_score(risky),
        )

    def test_windows_incompatible_candidate_is_not_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            researcher = MCPResearcher(
                output_dir=tmp,
                github=FakeGitHub(),
                fetch_text=fake_fetch,
            )
            candidate = researcher._candidate({
                "full_name": "linux/chrome-mcp",
                "html_url": "https://github.com/linux/chrome-mcp",
                "description": "Read-only MCP server for Chrome search and fetch",
                "stargazers_count": 100,
                "updated_at": "2026-10-20T00:00:00Z",
                "archived": False,
                "license": {"spdx_id": "MIT"},
                "topics": ["mcp-server"],
            }, (
                "Supported OS: Linux and macOS. Windows is not supported.\n"
                "## Available Tools\n"
                "- search\n- fetch\n"
                "Install with `docker run example/chrome-mcp`."
            ))
            self.assertFalse(candidate["eligible"])
            self.assertFalse(candidate["platform_compatible"])


if __name__ == "__main__":
    unittest.main()
