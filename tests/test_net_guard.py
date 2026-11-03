"""SSRF 守卫（`throat/net_guard.py`）与"重定向目标也要校验"（`net.py`）。

原来 `validate_public_http_url` 只校验用户给的**第一跳**，而 urllib 默认自动跟随 302：
`https://evil.com/x → Location: http://127.0.0.1:8787/api/...` 会被直接跟过去，
前面的校验等于白做。现在 direct/proxy 两个 opener 都装了守卫版 redirect handler。

全部用假 resolver / 假 opener，不联网。
"""
import pathlib
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import aiji.net as net  # noqa: E402
from aiji.throat.net_guard import UrlGuardError, validate_public_http_url  # noqa: E402

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


class ValidatePublicUrlTest(unittest.TestCase):
    def test_public_https_passes(self):
        parsed = validate_public_http_url("https://example.com/a.mp3",
                                          resolver=PUBLIC)
        self.assertEqual(parsed.hostname, "example.com")

    def test_non_http_scheme_rejected(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)",
                    "not a url", ""):
            with self.assertRaises(UrlGuardError) as ctx:
                validate_public_http_url(url, resolver=PUBLIC)
            self.assertEqual(ctx.exception.code, "invalid_url", url)

    def test_credentials_in_url_rejected(self):
        with self.assertRaises(UrlGuardError) as ctx:
            validate_public_http_url("https://user:pw@example.com/x", resolver=PUBLIC)
        self.assertEqual(ctx.exception.code, "invalid_url")

    def test_odd_port_rejected(self):
        with self.assertRaises(UrlGuardError) as ctx:
            validate_public_http_url("http://example.com:8080/x", resolver=PUBLIC)
        self.assertEqual(ctx.exception.code, "port_not_allowed")

    def test_allowed_ports_can_be_widened(self):
        parsed = validate_public_http_url("http://example.com:8080/x",
                                          resolver=PUBLIC, allowed_ports=(80, 443, 8080))
        self.assertEqual(parsed.port, 8080)

    def test_private_ranges_rejected(self):
        for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
                   "169.254.169.254", "0.0.0.0", "224.0.0.1"):
            with self.assertRaises(UrlGuardError) as ctx:
                validate_public_http_url("http://example.com/x",
                                         resolver=lambda host, ip=ip: [ip])
            self.assertEqual(ctx.exception.code, "private_url", ip)

    def test_literal_private_ip_rejected_without_resolver(self):
        with self.assertRaises(UrlGuardError) as ctx:
            validate_public_http_url("http://127.0.0.1/x")
        self.assertEqual(ctx.exception.code, "private_url")

    def test_dns_failure_is_reported(self):
        def boom(host):  # noqa: ARG001
            raise OSError("name resolution failed")

        with self.assertRaises(UrlGuardError) as ctx:
            validate_public_http_url("https://nope.example/x", resolver=boom)
        self.assertEqual(ctx.exception.code, "dns_failed")

    def test_empty_resolution_is_dns_failed(self):
        with self.assertRaises(UrlGuardError) as ctx:
            validate_public_http_url("https://nope.example/x", resolver=lambda h: [])
        self.assertEqual(ctx.exception.code, "dns_failed")

    def test_garbage_resolver_entries_are_ignored(self):
        # 解析结果里有非 IP 字符串时跳过，不能因此放行后面的私网地址
        with self.assertRaises(UrlGuardError):
            validate_public_http_url("http://example.com/x",
                                     resolver=lambda h: ["not-an-ip", "192.168.0.9"])
        self.assertEqual(
            validate_public_http_url("http://example.com/x",
                                     resolver=lambda h: ["not-an-ip"]).hostname,
            "example.com")


class RedirectGuardTest(unittest.TestCase):
    """直接调 handler 的 redirect_request，起一个假 req。"""

    def _handler(self):
        return net._GuardedRedirect()

    def test_redirect_to_private_address_is_refused(self):
        handler = self._handler()
        req = urllib.request.Request("https://evil.example/x")
        with mock.patch("aiji.throat.net_guard.validate_public_http_url",
                        side_effect=UrlGuardError("private_url", "no")) as guard:
            with self.assertRaises(UrlGuardError):
                handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:8787/x")
        guard.assert_called_once()

    def test_redirect_to_public_address_is_followed(self):
        handler = self._handler()
        req = urllib.request.Request("https://evil.example/x")
        with mock.patch("aiji.throat.net_guard.validate_public_http_url") as guard, \
             mock.patch.object(urllib.request.HTTPRedirectHandler, "redirect_request",
                               return_value="new-req"):
            out = handler.redirect_request(req, None, 302, "Found", {},
                                           "https://cdn.example/y")
        self.assertEqual(out, "new-req")
        guard.assert_called_once_with("https://cdn.example/y")

    def test_direct_opener_really_contains_the_guard(self):
        names = [type(h).__name__ for h in net._DIRECT.handlers]
        self.assertIn("_GuardedRedirect", names,
                      "直连 opener 没装守卫 → 重定向绕过 SSRF 校验")


class NetProxyFallbackTest(unittest.TestCase):
    def test_direct_success_does_not_touch_proxy(self):
        ok = mock.Mock()
        with mock.patch.object(net, "_DIRECT") as direct, \
             mock.patch.object(net, "_force_proxy", return_value=False):
            direct.open.return_value = ok
            self.assertIs(net.urlopen("req"), ok)
        direct.open.assert_called_once()

    def test_proxy_used_when_direct_fails_and_env_set(self):
        proxy = mock.Mock()
        proxy.open.return_value = "via-proxy"
        with mock.patch.object(net, "_DIRECT") as direct, \
             mock.patch.object(net, "_proxy_opener", return_value=proxy):
            direct.open.side_effect = urllib.error.URLError("refused")
            self.assertEqual(net.urlopen("req"), "via-proxy")

    def test_original_error_raised_when_proxy_also_fails(self):
        proxy = mock.Mock()
        proxy.open.side_effect = urllib.error.URLError("proxy down")
        with mock.patch.object(net, "_DIRECT") as direct, \
             mock.patch.object(net, "_proxy_opener", return_value=proxy):
            direct.open.side_effect = urllib.error.URLError("refused")
            with self.assertRaises(urllib.error.URLError) as ctx:
                net.urlopen("req")
        self.assertIn("refused", str(ctx.exception), "要抛直连的原始异常")

    def test_no_proxy_configured_reraises_direct_error(self):
        with mock.patch.object(net, "_DIRECT") as direct, \
             mock.patch.object(net, "_proxy_opener", return_value=None):
            direct.open.side_effect = OSError("boom")
            with self.assertRaises(OSError):
                net.urlopen("req")


if __name__ == "__main__":
    unittest.main()
