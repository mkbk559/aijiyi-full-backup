import unittest

from tools.proactive_audit_1h import decode_log, summarize


class ProactiveAuditTest(unittest.TestCase):
    def test_decodes_astrbot_windows_log(self):
        line = "AIJiYi proactive 发送失败: ApiNotAvailable:"
        self.assertEqual(decode_log(line.encode("gb18030")), line)

    def test_summary_counts_connection_and_send_outcomes(self):
        samples = [
            {"connected": False, "health_ok": True, "new_log": "AIJiYi proactive 发送失败: ApiNotAvailable:\n"},
            {"connected": True, "health_ok": True, "new_log": "AIJiYi proactive text -> group:123: 你好\n"},
            {"connected": False, "health_ok": False, "new_log": "AIJiYi proactive 发送失败: ApiNotAvailable:\n"},
        ]
        result = summarize(samples)
        self.assertEqual(result.get("connected_samples"), 1)
        self.assertEqual(result.get("health_failures"), 1)
        self.assertEqual(result.get("send_successes"), 1)
        self.assertEqual(result.get("api_not_available"), 2)


if __name__ == "__main__":
    unittest.main()
