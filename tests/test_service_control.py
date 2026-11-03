"""服务面板的生命周期探测：netstat 解析、端口状态、进程名、保活任务开关。

这块代码是"控制台按钮"的手：`start/stop/restart` 会真的拉起脚本，
`watchdog-on/off` 会真的注册/删除计划任务。写错一个字就是"点了没反应"，
或者更糟——删掉用户手动注册的别的任务。所以每个动作都验到**命令行参数**，
每个探测都验到**解析结果**，不碰真服务。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.right_hand import service_control as sc  # noqa: E402

# 真实 netstat 的一行截断样本（协议 本地地址 远端地址 状态 PID）
NETSTAT = """\
活动连接

  协议  本地地址          外部地址        状态           PID
  TCP    0.0.0.0:8787       0.0.0.0:0       LISTENING       4321
  TCP    127.0.0.1:6199     127.0.0.1:5555  ESTABLISHED     4321
  TCP    127.0.0.1:6199     127.0.0.1:5556  ESTABLISHED     4321
  TCP    127.0.0.1:8787     127.0.0.1:6001  ESTABLISHED     4321
  TCP    0.0.0.0:6099       0.0.0.0:0       LISTENING       abc
  TCPV6   [::]:8899          [::]:0          LISTENING       777
  UDP    0.0.0.0:5000       *:*                             999
"""


class RunTest(unittest.TestCase):
    def test_stdout_and_stderr_are_combined(self):
        done = types.SimpleNamespace(returncode=3, stdout="出", stderr="错")
        with mock.patch.object(sc.subprocess, "run", return_value=done):
            code, text = sc._run(["x"])
        self.assertEqual((code, text), (3, "出错"))

    def test_oserror_and_timeout_are_reported_not_raised(self):
        with mock.patch.object(sc.subprocess, "run", side_effect=OSError("没有这个命令")):
            self.assertEqual(sc._run(["x"]), (-1, "没有这个命令"))
        with mock.patch.object(sc.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 1)):
            code, text = sc._run(["x"])
        self.assertEqual(code, -1)
        self.assertIn("timed out", text)


class LocalPortTest(unittest.TestCase):
    def test_parses_trailing_port(self):
        self.assertEqual(sc._local_port("127.0.0.1:8787"), 8787)
        self.assertEqual(sc._local_port("[::]:8899"), 8899)

    def test_rejects_junk(self):
        self.assertIsNone(sc._local_port("8787"))
        self.assertIsNone(sc._local_port("0.0.0.0:abc"))
        self.assertIsNone(sc._local_port(":"))


class NetstatTest(unittest.TestCase):
    def setUp(self):
        sc._rows.update(at=0.0, rows=[])

    def test_parses_only_tcp_rows(self):
        with mock.patch.object(sc, "_run", return_value=(0, NETSTAT)):
            rows = sc._netstat_rows()
        self.assertEqual(len(rows), 6, "UDP 行要丢掉")
        self.assertTrue(all(len(r) == 5 for r in rows))
        self.assertEqual(rows[0][3], "LISTENING")

    def test_snapshot_is_cached_for_two_seconds(self):
        calls = []

        def fake_run(args, timeout=6.0):
            calls.append(args)
            return 0, NETSTAT

        with mock.patch.object(sc, "_run", fake_run):
            sc._netstat_rows()
            sc._netstat_rows()
        self.assertEqual(len(calls), 1, "2 秒内必须复用同一份 netstat")

    def test_cache_expires(self):
        calls = []
        with mock.patch.object(sc, "_run",
                               lambda *a, **k: (calls.append(1), (0, NETSTAT))[1]):
            sc._netstat_rows()
            sc._rows["at"] -= 5.0
            sc._netstat_rows()
        self.assertEqual(len(calls), 2)


class PortStateTest(unittest.TestCase):
    def setUp(self):
        sc._rows.update(at=0.0, rows=[])

    def test_listening_pid_and_established_count(self):
        with mock.patch.object(sc, "_run", return_value=(0, NETSTAT)):
            listening, connections, pid = sc._port_state(6199)
        self.assertTrue(not listening, "6199 只有 ESTABLISHED")
        self.assertEqual(connections, 2)
        self.assertIsNone(pid)

        with mock.patch.object(sc, "_run", return_value=(0, NETSTAT)):
            listening, connections, pid = sc._port_state(8787)
        self.assertEqual((listening, pid), (True, 4321))
        self.assertEqual(connections, 1)

    def test_non_numeric_owner_is_none(self):
        with mock.patch.object(sc, "_run", return_value=(0, NETSTAT)):
            listening, _connections, pid = sc._port_state(6099)
        self.assertTrue(listening)
        self.assertIsNone(pid)


class ProcessNameTest(unittest.TestCase):
    def setUp(self):
        sc._process_cache.clear()

    def test_stale_pids_are_resolved_in_one_powershell_call(self):
        seen = []

        def fake_run(args, timeout=6.0):
            seen.append(args)
            return 0, "4321 python\n777 NapCatWinBootMain\n"

        with mock.patch.object(sc, "_run", fake_run):
            names = sc._resolve_processes({4321, 777})
        self.assertEqual(names, {4321: "python", 777: "NapCatWinBootMain"})
        self.assertEqual(len(seen), 1, "多个 PID 只查一次")
        self.assertIn("Get-Process -Id 4321,777", seen[0][-1])

    def test_fresh_cache_skips_lookup(self):
        sc._process_cache[4321] = ("python", sc.time.monotonic())
        with mock.patch.object(sc, "_run") as run:
            names = sc._resolve_processes({4321})
        self.assertEqual(names, {4321: "python"})
        run.assert_not_called()

    def test_empty_and_zero_pids(self):
        self.assertEqual(sc._resolve_processes(set()), {})
        self.assertEqual(sc._resolve_processes({0}), {})

    def test_unparsable_output_leaves_empty_name(self):
        with mock.patch.object(sc, "_run", return_value=(0, "ERROR: 拒绝访问")):
            names = sc._resolve_processes({555})
        self.assertEqual(names, {555: ""})

    def test_staleness_window(self):
        self.assertTrue(sc._process_stale(1))
        sc._process_cache[1] = ("x", sc.time.monotonic())
        self.assertFalse(sc._process_stale(1))
        sc._process_cache[1] = ("x", sc.time.monotonic() - 500)
        self.assertTrue(sc._process_stale(1))


class ListeningTest(unittest.TestCase):
    def setUp(self):
        sc._rows.update(at=0.0, rows=[])

    def test_netstat_answer_avoids_tcp_connect(self):
        with mock.patch.object(sc, "_run", return_value=(0, NETSTAT)), \
                mock.patch.object(sc.socket, "create_connection") as connect:
            self.assertTrue(sc._listening(8787))
        connect.assert_not_called()

    def test_presence_port_never_gets_a_bare_connect(self):
        """presence 是 WebSocket 端口：空连接会在对方日志里刷一条 ERROR traceback。"""
        with mock.patch.object(sc, "_run", return_value=(0, "")), \
                mock.patch.object(sc.socket, "create_connection") as connect:
            self.assertFalse(sc._listening(8789))
        connect.assert_not_called()

    def test_connect_fallback_for_plain_ports(self):
        with mock.patch.object(sc, "_run", return_value=(0, "")), \
                mock.patch.object(sc.socket, "create_connection",
                                  return_value=mock.MagicMock()) as connect:
            self.assertTrue(sc._listening(9999))
        self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 9999))

    def test_connect_failure_is_false(self):
        with mock.patch.object(sc, "_run", return_value=(0, "")), \
                mock.patch.object(sc.socket, "create_connection",
                                  side_effect=OSError("拒绝")):
            self.assertFalse(sc._listening(9999))


class StateFilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "svc"))

    def test_desired_state(self):
        state = self.dir / "state.txt"
        with mock.patch.object(sc, "STATE_FILE", state):
            self.assertIsNone(sc.desired_state(), "文件不存在就是没下过指令")
            state.write_text("running", encoding="utf-8")
            self.assertEqual(sc.desired_state(), "running")
            state.write_text("   ", encoding="utf-8")
            self.assertIsNone(sc.desired_state(), "空白等于没写")

    def test_startup_entry(self):
        startup = self.dir / "Startup"
        startup.mkdir()
        with mock.patch.dict(sc.os.environ, {}, clear=True):
            self.assertEqual(sc._startup_entry(), "", "没有 APPDATA 就查不了")
        real = (self.dir / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Startup")
        real.mkdir(parents=True)
        with mock.patch.dict(sc.os.environ, {"APPDATA": str(self.dir)}, clear=True):
            self.assertEqual(sc._startup_entry(), "", "目录里没有 aiji 项")
            (real / "AIJiYi 自启.lnk").write_text("x", encoding="utf-8")
            self.assertEqual(sc._startup_entry(), "AIJiYi 自启.lnk")

    def test_startup_entry_survives_missing_folder(self):
        """APPDATA 设了但 Startup 目录不存在（新装机器/换了用户）不能炸。"""
        empty = self.dir / "空的"
        empty.mkdir()
        with mock.patch.dict(sc.os.environ, {"APPDATA": str(empty)}, clear=True):
            self.assertEqual(sc._startup_entry(), "")

    def test_log_tail(self):
        log = self.dir / "lifecycle.log"
        with mock.patch.object(sc, "LOG_FILE", log):
            out = sc.log_tail()
            self.assertFalse(out["exists"])
            self.assertEqual(out["lines"], [])
            log.write_text("第一行\n\n第二行\n", encoding="utf-8")
            out = sc.log_tail()
        self.assertTrue(out["exists"])
        self.assertEqual(out["lines"], ["第一行", "第二行"], "空行要过滤")
        self.assertIsNotNone(out["updated_at"])

    def test_prerequisites_states(self):
        items = sc.prerequisites()
        keys = {item["name"]: item for item in items}
        self.assertTrue(keys["运行配置"]["optional"])
        for item in items:
            self.assertIn(item["state"], ("ok", "optional", "missing"))


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "wd"))
        self.task_file = self.dir / "Tasks" / sc.WATCHDOG_TASK
        self.task_file.parent.mkdir(parents=True, exist_ok=True)
        patcher = mock.patch.object(sc, "TASK_FILE", self.task_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_registered(self):
        with mock.patch.object(sc, "_run", return_value=(0, "任务名  下次运行时间")), \
                mock.patch.object(sc, "_startup_entry", return_value=""):
            out = sc.watchdog()
        self.assertTrue(out["registered"])
        self.assertEqual(out["detail"], "")
        self.assertFalse(out["startup"])

    def test_not_registered_uses_first_line_as_detail(self):
        with mock.patch.object(sc, "_run", return_value=(1, "\n错误: 没有这个任务\n")), \
                mock.patch.object(sc, "_startup_entry", return_value="aiji.lnk"):
            out = sc.watchdog()
        self.assertFalse(out["registered"])
        self.assertEqual(out["detail"], "错误: 没有这个任务")
        self.assertTrue(out["startup"])

    def test_empty_output_falls_back_to_exit_code(self):
        with mock.patch.object(sc, "_run", return_value=(5, "")), \
                mock.patch.object(sc, "_startup_entry", return_value=""):
            out = sc.watchdog()
        self.assertEqual(out["detail"], "schtasks 退出码 5")

    def test_task_file_is_second_witness(self):
        self.task_file.write_text("定义", encoding="utf-8")
        with mock.patch.object(sc, "_run", return_value=(1, "拒绝访问")), \
                mock.patch.object(sc, "_startup_entry", return_value=""):
            out = sc.watchdog()
        self.assertTrue(out["registered"], "查询失败但定义文件在，要算已注册")
        self.assertIn("计划任务定义文件", out["detail"])

    def test_watchdog_on_requires_nt_and_script(self):
        with mock.patch.object(sc.os, "name", "posix"):
            with self.assertRaises(RuntimeError):
                sc._watchdog_on()
        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "WATCHDOG_SCRIPT", self.dir / "缺.ps1"):
            with self.assertRaises(RuntimeError) as ctx:
                sc._watchdog_on()
        self.assertIn("缺少保活脚本", str(ctx.exception))

    def test_watchdog_on_registers_five_minute_task(self):
        script = self.dir / "ensure_console.ps1"
        script.write_text("#", encoding="utf-8")
        seen = {}

        def fake_run(args, timeout=6.0):
            seen["args"], seen["timeout"] = args, timeout
            return 0, "成功"

        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "WATCHDOG_SCRIPT", script), \
                mock.patch.object(sc, "_run", fake_run):
            out = sc._watchdog_on()
        self.assertEqual(out["action"], "watchdog-on")
        self.assertIn("/Create", seen["args"])
        self.assertEqual(seen["args"][seen["args"].index("/SC") + 1], "MINUTE")
        self.assertEqual(seen["args"][seen["args"].index("/MO") + 1], "5")
        self.assertIn(str(script), seen["args"][seen["args"].index("/TR") + 1])

    def test_watchdog_on_reports_schtasks_error(self):
        script = self.dir / "ensure_console.ps1"
        script.write_text("#", encoding="utf-8")
        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "WATCHDOG_SCRIPT", script), \
                mock.patch.object(sc, "_run", return_value=(1, "拒绝访问")):
            with self.assertRaises(RuntimeError) as ctx:
                sc._watchdog_on()
        self.assertIn("拒绝访问", str(ctx.exception))

    def test_watchdog_off_when_absent(self):
        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "watchdog", return_value={"registered": False}):
            out = sc._watchdog_off()
        self.assertIn("本来就不存在", out["detail"])

    def test_watchdog_off_deletes(self):
        seen = {}
        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "watchdog", return_value={"registered": True}), \
                mock.patch.object(sc, "_run",
                                  lambda args, timeout=6.0: (seen.update(args=args), (0, ""))[1]):
            out = sc._watchdog_off()
        self.assertEqual(out["detail"], "保活任务已注销")
        self.assertIn("/Delete", seen["args"])

    def test_watchdog_off_reports_failure(self):
        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "watchdog", return_value={"registered": True}), \
                mock.patch.object(sc, "_run", return_value=(1, "")):
            with self.assertRaises(RuntimeError) as ctx:
                sc._watchdog_off()
        self.assertIn("schtasks 退出码 1", str(ctx.exception))

    def test_watchdog_off_requires_nt(self):
        with mock.patch.object(sc.os, "name", "posix"):
            with self.assertRaises(RuntimeError):
                sc._watchdog_off()


class LaunchTest(unittest.TestCase):
    def test_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            sc.launch("format-c")
        with self.assertRaises(ValueError):
            sc.control("format-c")

    def test_requires_windows_and_script(self):
        with mock.patch.object(sc.os, "name", "posix"), \
                mock.patch.object(sc, "SCRIPT", pathlib.Path("x.ps1")):
            with self.assertRaises(RuntimeError):
                sc.launch("start")
        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "SCRIPT", pathlib.Path("不存在.ps1")):
            with self.assertRaises(RuntimeError):
                sc.launch("start")

    def test_spawns_with_expected_arguments(self):
        script = pathlib.Path(make_tmp(self, "launch")) / "tools" / "lifecycle.ps1"
        script.parent.mkdir(parents=True)
        script.write_text("#", encoding="utf-8")
        with mock.patch.object(sc.os, "name", "nt"), \
                mock.patch.object(sc, "SCRIPT", script), \
                mock.patch.object(sc.subprocess, "Popen") as popen:
            sc.launch("restart")
        args = popen.call_args.args[0]
        self.assertEqual(args[:1], ["powershell"])
        self.assertEqual(args[args.index("-File") + 1], str(script))
        self.assertEqual(args[args.index("-Action") + 1], "restart")
        self.assertEqual(popen.call_args.kwargs["cwd"], script.parents[1])
        self.assertTrue(popen.call_args.kwargs["creationflags"])

    def test_control_dispatches(self):
        with mock.patch.object(sc, "launch") as launch:
            out = sc.control("stop")
        self.assertEqual(out["action"], "stop")
        launch.assert_called_once_with("stop")
        with mock.patch.object(sc, "_watchdog_on", return_value={"action": "watchdog-on"}):
            self.assertEqual(sc.control("watchdog-on")["action"], "watchdog-on")
        with mock.patch.object(sc, "_watchdog_off", return_value={"action": "watchdog-off"}):
            self.assertEqual(sc.control("watchdog-off")["action"], "watchdog-off")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "snap"))

    def _snapshot(self, listening_map, port_state=None):
        state = port_state or (lambda port: (listening_map.get(port, False), 0, None))
        fake_os = types.SimpleNamespace(name="nt", environ={}, getpid=lambda: 4242)
        with mock.patch.object(sc, "os", fake_os), \
                mock.patch.object(sc, "_listening",
                                  lambda port: listening_map.get(port, False)), \
                mock.patch.object(sc, "_port_state", state), \
                mock.patch.object(sc, "_resolve_processes", return_value={}), \
                mock.patch.object(sc, "desired_state", return_value="running"), \
                mock.patch.object(sc, "watchdog", return_value={"registered": True}), \
                mock.patch.object(sc, "prerequisites", return_value=[]), \
                mock.patch.object(sc, "log_tail", return_value={"lines": []}), \
                mock.patch.object(sc, "SCRIPT", self.dir / "lifecycle.ps1"):
            return sc.snapshot()

    def test_all_stopped_reports_first_blocker(self):
        snap = self._snapshot({})
        self.assertEqual(snap["ready"], 0)
        self.assertFalse(snap["all_ready"])
        self.assertEqual(snap["blocked"]["id"], "napcat")
        self.assertEqual(snap["blocked"]["state"], "stopped")
        self.assertIn("QQ", snap["blocked"]["hint"])
        self.assertEqual(snap["desired"]["state"], "running")

    def test_all_ready(self):
        def state(port):
            # onebot 还要求有 QQ 真连上来（ESTABLISHED 计数 > 0）
            return True, (1 if port == 6199 else 0), None

        snap = self._snapshot({6099: True, 6199: True, 6185: True, 8787: True,
                               8789: True}, port_state=state)
        self.assertEqual(snap["ready"], snap["total"])
        self.assertTrue(snap["all_ready"])
        self.assertIsNone(snap["blocked"])

    def test_onebot_requires_an_established_connection(self):
        def state(port):
            if port == 6199:
                return False, 1, 900      # 端口在听 + 有连接
            return True, 0, None

        snap = self._snapshot({6199: True, 6099: True, 6185: True, 8787: True,
                               8789: True}, port_state=state)
        onebot = next(i for i in snap["services"] if i["id"] == "onebot")
        self.assertTrue(onebot["running"], "onebot 看的是有没有 QQ 连上来")
        self.assertEqual(onebot["connections"], 1)

    def test_own_pid_is_labelled(self):
        def state(port):
            return (port == 8787), 0, (4242 if port == 8787 else None)

        snap = self._snapshot({8787: True}, port_state=state)
        aiji = next(i for i in snap["services"] if i["id"] == "aiji")
        self.assertEqual(aiji["process"], "aiji 核心（本进程）")

    def test_probe_failure_still_renders_panel(self):
        """netstat 探不通（OSError）时整块面板仍要出结果，不能 500。"""

        def boom(_port):
            raise OSError("netstat 没了")

        fake_os = types.SimpleNamespace(name="nt", environ={}, getpid=lambda: 1)
        with mock.patch.object(sc, "os", fake_os), \
                mock.patch.object(sc, "_listening", boom), \
                mock.patch.object(sc, "_port_state", boom), \
                mock.patch.object(sc, "desired_state", return_value=None), \
                mock.patch.object(sc, "watchdog", return_value={"registered": False}), \
                mock.patch.object(sc, "prerequisites", return_value=[]), \
                mock.patch.object(sc, "log_tail", return_value={"lines": []}), \
                mock.patch.object(sc, "SCRIPT", self.dir / "lifecycle.ps1"):
            snap = sc.snapshot()
        self.assertEqual(snap["ready"], 0)
        self.assertTrue(all(not s["running"] for s in snap["services"]))
        self.assertEqual(snap["blocked"]["id"], "napcat")

    def test_unknown_process_name_is_blank(self):
        def state(port):
            return (port == 8787), 0, 4321

        snap = self._snapshot({8787: True}, port_state=state)
        aiji = next(i for i in snap["services"] if i["id"] == "aiji")
        self.assertEqual(aiji["process"], "", "查不到进程名就留空，别编")

    def test_actions_hidden_when_script_missing(self):
        fake_os = types.SimpleNamespace(name="nt", environ={}, getpid=lambda: 1)
        with mock.patch.object(sc, "os", fake_os), \
                mock.patch.object(sc, "_listening", return_value=False), \
                mock.patch.object(sc, "_port_state", return_value=(False, 0, None)), \
                mock.patch.object(sc, "desired_state", return_value=None), \
                mock.patch.object(sc, "watchdog", return_value={"registered": False}), \
                mock.patch.object(sc, "prerequisites", return_value=[]), \
                mock.patch.object(sc, "log_tail", return_value={"lines": []}), \
                mock.patch.object(sc, "SCRIPT", self.dir / "lifecycle.ps1"):
            snap = sc.snapshot()
        self.assertFalse(snap["actions_available"], "脚本不存在时不能显示可操作")
        self.assertEqual(snap["ready"], 0)


if __name__ == "__main__":
    unittest.main()
