"""服务端错误处理：客户端断开不该打 traceback，真异常照旧打。

实测 `data/console.err.*.log` 里全是 `ConnectionResetError: [WinError 10054]` +
socketserver 栈（浏览器提前关连接就会这样），把真问题都埋了。
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.throat import server as srv  # noqa: E402


class HandleErrorTest(unittest.TestCase):
    def _server(self):
        httpd = srv.AijiHTTPServer.__new__(srv.AijiHTTPServer)
        httpd.checkup_cache = {}
        return httpd

    def test_client_reset_is_quiet(self):
        httpd = self._server()
        httpd.socket = None
        with mock.patch.object(srv.logger, "debug") as dbg, \
             mock.patch("socketserver.BaseServer.handle_error") as parent:
            try:
                raise ConnectionResetError(10054, "远程主机强迫关闭了连接")
            except ConnectionResetError:
                httpd.handle_error(None, ("127.0.0.1", 1234))
        self.assertEqual(parent.call_count, 0, "连接重置不该落到默认处理")
        self.assertEqual(dbg.call_count, 1)
        self.assertIn("ConnectionResetError", dbg.call_args[0][-1])

    def test_broken_pipe_and_abort_are_quiet_too(self):
        httpd = self._server()
        httpd.socket = None
        for exc_type in (BrokenPipeError, ConnectionAbortedError):
            with mock.patch("socketserver.BaseServer.handle_error") as parent:
                try:
                    raise exc_type("断了")
                except exc_type:
                    httpd.handle_error(None, ("127.0.0.1", 1))
            self.assertEqual(parent.call_count, 0, f"{exc_type.__name__} 应该静默")

    def test_real_errors_still_reach_default_handler(self):
        httpd = self._server()
        httpd.socket = None
        with mock.patch.object(srv.logger, "debug") as dbg, \
             mock.patch("socketserver.BaseServer.handle_error") as parent:
            try:
                raise ValueError("真 bug")
            except ValueError:
                httpd.handle_error(None, ("127.0.0.1", 1))
        self.assertEqual(parent.call_count, 1, "真异常必须照旧打 traceback")
        self.assertEqual(dbg.call_count, 0)


class ListenBacklogTest(unittest.TestCase):
    """socketserver 默认 backlog=5，实测 8 线程并发就让 p95 从 29ms 跳到 522ms。

    522 ≈ Windows 的 SYN 重传间隔：连接在 backlog 里排不下，客户端在重传。
    这条断言防的是"有人把 request_queue_size 那一行删掉/改小"。
    """

    def test_backlog_is_raised_above_socketserver_default(self):
        import socketserver

        self.assertGreater(
            srv.AijiHTTPServer.request_queue_size,
            socketserver.TCPServer.request_queue_size,
            "请求队列必须大于 socketserver 默认值（5）")
        self.assertGreaterEqual(srv.AijiHTTPServer.request_queue_size, 64)


if __name__ == "__main__":
    unittest.main()
