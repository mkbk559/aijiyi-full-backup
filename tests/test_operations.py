"""Operations page: editable settings, truthful inventory, live lifecycle probes."""
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

from aiji import service_control
from aiji.config import Settings
from aiji.operations import catalog, read_config, save_config

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


def workdir() -> Path:
    path = TMP_ROOT / f"operations-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    return path


class TestOperations(unittest.TestCase):
    def test_settings_round_trip_and_secret_redaction(self):
        path = workdir() / "runtime.json"
        try:
            saved = save_config(path, {"budget_ms": 160, "rank_mode": "rrf_shadow",
                                       "sticker_update": False, "onebot_group_mode": "all",
                                       "qq_auto_interject_chance": 0.4})
            self.assertEqual(saved["values"]["budget_ms"], 160)
            self.assertEqual(saved["values"]["rank_mode"], "rrf_shadow")
            self.assertEqual(saved["values"]["qq_auto_interject_chance"], 0.4)
            self.assertNotIn("onebot_token", saved["values"])
            loaded = Settings.load(path)
            self.assertEqual(loaded.budget_ms, 160)
            self.assertEqual(loaded.rank_mode, "rrf_shadow")
            self.assertFalse(loaded.sticker_update)
            self.assertEqual(loaded.onebot_group_mode, "all")
            self.assertEqual(read_config(path)["values"]["budget_ms"], 160)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_invalid_or_secret_keys_are_rejected_without_writing(self):
        path = workdir() / "runtime.json"
        try:
            for change in ({"budget_ms": -1}, {"onebot_token": "secret"},
                           {"rank_mode": "unknown"}, {"not_a_setting": 1},
                           {"qq_auto_interject_chance": 0.9}):
                with self.assertRaises(ValueError):
                    save_config(path, change)
            self.assertFalse(path.exists())
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_config_exposes_defaults_for_the_reset_button(self):
        config = read_config(workdir() / "runtime.json")
        defaults = Settings()
        self.assertEqual(config["defaults"]["budget_ms"], defaults.budget_ms)
        self.assertEqual(config["defaults"]["rank_mode"], defaults.rank_mode)
        self.assertEqual(set(config["defaults"]), {key for keys in config["groups"].values()
                                                   for key in keys})

    def test_catalog_has_real_endpoints_and_workflows(self):
        items = catalog()
        self.assertGreaterEqual(len(items), 18)
        self.assertTrue(all(item["name"] and item["description"] and item["endpoint"]
                            for item in items))
        self.assertIn("/api/memories", {item["endpoint"] for item in items})
        self.assertIn("/api/proactive", {item["endpoint"] for item in items})
        self.assertTrue(all(item["detail"] and item["requirements"] for item in items))

    def test_service_action_accepts_only_fixed_commands(self):
        with self.assertRaises(ValueError):
            service_control.launch("powershell arbitrary")
        with mock.patch.object(service_control, "os") as fake_os, \
             mock.patch.object(service_control.subprocess, "Popen") as popen:
            fake_os.name = "nt"
            with mock.patch.object(type(service_control.SCRIPT), "exists", return_value=True):
                service_control.launch("start")
            args = popen.call_args.args[0]
            self.assertEqual(args[-1], "start")

    def test_qq_reverse_status_requires_established_connection(self):
        with mock.patch.object(service_control, "_listening", return_value=True), \
             mock.patch.object(service_control, "_established", return_value=False):
            services = {item["id"]: item for item in service_control.snapshot()["services"]}
        self.assertFalse(services["onebot"]["running"])
        self.assertTrue(services["astrbot"]["running"])

    def test_snapshot_reports_chain_blocked_point_and_diagnostics(self):
        def listening(port: int) -> bool:
            return port in (6099, 6199, 6185, 8787, 8789)

        with mock.patch.object(service_control, "_listening", side_effect=listening), \
             mock.patch.object(service_control, "_established", return_value=False), \
             mock.patch.object(service_control, "_port_state", return_value=(True, 0, 4242)), \
             mock.patch.object(service_control, "_resolve_processes",
                               side_effect=lambda pids: {pid: "python.exe" for pid in pids}), \
             mock.patch.object(service_control, "watchdog", return_value={
                 "task": "AIJiYi Console Watchdog", "registered": False, "detail": "missing",
                 "startup": True, "startup_entry": "AIJiYi Startup.lnk", "supported": True}), \
             mock.patch.object(service_control, "log_tail", return_value={
                 "path": "lifecycle.log", "exists": True, "lines": ["a", "b"],
                 "updated_at": "2026-11-02 10:50:00"}):
            snap = service_control.snapshot()

        services = {item["id"]: item for item in snap["services"]}
        self.assertEqual(snap["order"],
                         ["napcat", "onebot", "astrbot", "aiji", "presence"])
        self.assertEqual(services["onebot"]["state"], "partial")
        self.assertFalse(services["onebot"]["running"])
        self.assertTrue(services["onebot"]["listening"])
        self.assertEqual(services["aiji"]["pid"], 4242)
        self.assertEqual(services["aiji"]["process"], "python.exe")
        self.assertEqual(snap["ready"], 4)
        self.assertFalse(snap["all_ready"])
        self.assertEqual(snap["blocked"]["id"], "onebot")
        self.assertTrue(snap["blocked"]["hint"])
        self.assertEqual(snap["log"]["lines"], ["a", "b"])
        self.assertTrue(snap["checked_at"])

    def test_desired_state_reads_the_stop_marker(self):
        folder = workdir()
        marker = folder / "service_state.txt"
        try:
            with mock.patch.object(service_control, "STATE_FILE", marker):
                self.assertIsNone(service_control.desired_state())
                marker.write_text("stopped", "utf-8")
                self.assertEqual(service_control.desired_state(), "stopped")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_log_tail_returns_last_lines_only(self):
        folder = workdir()
        log = folder / "lifecycle.log"
        try:
            log.write_text("\n".join(f"line {index}" for index in range(50)), "utf-8")
            with mock.patch.object(service_control, "LOG_FILE", log):
                tail = service_control.log_tail(limit=5)
            self.assertTrue(tail["exists"])
            self.assertEqual(tail["lines"], [f"line {index}" for index in range(45, 50)])
            self.assertTrue(tail["updated_at"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_control_rejects_unknown_actions(self):
        with self.assertRaises(ValueError):
            service_control.control("sleep")
        with self.assertRaises(ValueError):
            service_control.control("")

    def test_watchdog_registers_fixed_task(self):
        with mock.patch.object(service_control, "os") as fake_os, \
             mock.patch.object(type(service_control.WATCHDOG_SCRIPT), "exists", return_value=True), \
             mock.patch.object(service_control, "_run", return_value=(0, "SUCCESS")) as run:
            fake_os.name = "nt"
            result = service_control.control("watchdog-on")
            args = run.call_args.args[0]
        self.assertEqual(result["action"], "watchdog-on")
        self.assertEqual(args[:4], ["schtasks", "/Create", "/TN", "AIJiYi Console Watchdog"])
        self.assertIn("/SC", args)
        self.assertEqual(args[args.index("/SC") + 1], "MINUTE")
        self.assertIn("/F", args)
        self.assertIn(str(service_control.WATCHDOG_SCRIPT), args[args.index("/TR") + 1])

    def test_watchdog_off_is_idempotent_and_errors_are_reported(self):
        with mock.patch.object(service_control, "os") as fake_os, \
             mock.patch.object(service_control, "watchdog", return_value={"registered": False}):
            fake_os.name = "nt"
            self.assertIn("本来就不存在", service_control.control("watchdog-off")["detail"])
        with mock.patch.object(service_control, "os") as fake_os, \
             mock.patch.object(type(service_control.WATCHDOG_SCRIPT), "exists", return_value=True), \
             mock.patch.object(service_control, "_run", return_value=(1, "拒绝访问")):
            fake_os.name = "nt"
            with self.assertRaises(RuntimeError):
                service_control.control("watchdog-on")


if __name__ == "__main__":
    unittest.main()
