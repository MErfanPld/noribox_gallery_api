"""Regression tests for proxy/IP handling, throttling, SSRF and production settings."""
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest import mock, skipUnless

from django.core.cache.backends.locmem import LocMemCache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.throttling import SimpleRateThrottle

from accounts.models import OTPCode
from core.http import FetchError, UnsafeURLError, safe_fetch, validate_url
from core.net import get_client_ip
from core.testutils import client_for, make_user
from core.throttling import ScopedRateThrottle
from products.services.importer import SourceUnavailableError, fetch_html, iterate_listing_pages
from products.services.review import download_image

BASE_DIR = Path(__file__).resolve().parents[2]
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


def redis_available():
    try:
        socket.create_connection(("127.0.0.1", 6379), timeout=0.5).close()
        return True
    except OSError:
        return False


class ClientIPTests(SimpleTestCase):
    rf = RequestFactory()

    def ip(self, remote, **headers):
        return get_client_ip(self.rf.get("/", REMOTE_ADDR=remote, **headers))

    def test_untrusted_peer_cannot_choose_identity(self):
        # No trusted proxies: forwarded headers are ignored entirely.
        self.assertEqual(self.ip("203.0.113.7", HTTP_X_FORWARDED_FOR="1.2.3.4", HTTP_X_REAL_IP="5.6.7.8"), "203.0.113.7")

    @override_settings(TRUSTED_PROXIES=["127.0.0.1/32"])
    def test_trusted_proxy_headers(self):
        self.assertEqual(self.ip("127.0.0.1", HTTP_X_REAL_IP="198.51.100.9"), "198.51.100.9")
        # Client-forged left-most XFF entries are ignored; right-most untrusted hop wins.
        self.assertEqual(self.ip("127.0.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6, 198.51.100.9"), "198.51.100.9")
        self.assertEqual(self.ip("127.0.0.1", HTTP_X_FORWARDED_FOR="garbage"), "127.0.0.1")
        # A non-proxy peer sending proxy headers is still identified by its socket address.
        self.assertEqual(self.ip("203.0.113.7", HTTP_X_REAL_IP="198.51.100.9"), "203.0.113.7")


def low_rate(rate="2/min"):
    rates = {**SimpleRateThrottle.THROTTLE_RATES, "auth": rate, "otp_request": rate}
    return mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates)


class ThrottleTests(TestCase):
    def setUp(self):
        SimpleRateThrottle.cache.clear()

    def login(self, client, **headers):
        return client.post("/api/v1/auth/login/", {"phone": "09121234567", "password": "x"}, **headers).status_code

    def test_rotating_forged_xff_does_not_bypass_throttle(self):
        with low_rate():
            c = client_for()
            codes = [self.login(c, HTTP_X_FORWARDED_FOR=f"10.9.8.{i}", HTTP_X_REAL_IP=f"10.9.7.{i}") for i in range(5)]
        self.assertEqual(codes[:2], [400, 400])
        self.assertEqual(codes[2:], [429, 429, 429])

    @override_settings(TRUSTED_PROXIES=["127.0.0.1/32"])
    def test_distinct_real_clients_behind_trusted_proxy_have_separate_buckets(self):
        with low_rate():
            c = client_for()
            a = [self.login(c, REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="198.51.100.1") for _ in range(3)]
            b = self.login(c, REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="198.51.100.2")
        self.assertEqual(a, [400, 400, 429])
        self.assertEqual(b, 400)

    @override_settings(TRUSTED_PROXIES=["127.0.0.1/32"])
    def test_otp_ip_logged_from_trusted_proxy(self):
        client_for().post("/api/v1/auth/otp/request/", {"phone": "09121230000"}, REMOTE_ADDR="127.0.0.1",
                          HTTP_X_REAL_IP="198.51.100.4")
        self.assertEqual(OTPCode.objects.get().ip_address, "198.51.100.4")

    def _throttle_on(self, cache, request):
        t = ScopedRateThrottle()
        t.cache = cache
        view = mock.Mock(throttle_scope="auth")
        return t.allow_request(request, view)

    @skipUnless(redis_available(), "Redis server not reachable")
    def test_independent_workers_share_redis_throttle_state(self):
        from django.core.cache.backends.redis import RedisCache

        worker_a = RedisCache(REDIS_URL, {"KEY_PREFIX": "throttle-test"})
        worker_b = RedisCache(REDIS_URL, {"KEY_PREFIX": "throttle-test"})
        worker_a.clear()
        req = RequestFactory().post("/", REMOTE_ADDR="203.0.113.50")
        req.user = mock.Mock(is_authenticated=False)
        with low_rate("3/min"):
            results = [self._throttle_on(w, req) for w in (worker_a, worker_b, worker_a, worker_b, worker_a)]
        worker_a.clear()
        self.assertEqual(results, [True, True, True, False, False])

    def test_locmem_is_not_shared_negative_control(self):
        # Documents why production requires REDIS_URL: per-process caches don't share counters.
        a, b = LocMemCache("a", {}), LocMemCache("b", {})
        req = RequestFactory().post("/", REMOTE_ADDR="203.0.113.51")
        req.user = mock.Mock(is_authenticated=False)
        with low_rate("1/min"):
            self.assertEqual([self._throttle_on(a, req), self._throttle_on(b, req)], [True, True])


@override_settings(LOGIN_MAX_FAILURES=3)
class LoginLockoutTests(TestCase):
    def setUp(self):
        SimpleRateThrottle.cache.clear()

    def test_per_phone_lockout_independent_of_ip(self):
        user = make_user("09127770000")
        for i in range(3):
            self.assertEqual(client_for().post("/api/v1/auth/login/", {"phone": user.phone, "password": "bad"},
                                               REMOTE_ADDR=f"203.0.113.{i}").status_code, 400)
        res = client_for().post("/api/v1/auth/login/", {"phone": user.phone, "password": "S3cure-pass!"}, REMOTE_ADDR="203.0.113.99")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["error"]["code"], "login_locked")
        SimpleRateThrottle.cache.clear()
        ok = client_for().post("/api/v1/auth/login/", {"phone": user.phone, "password": "S3cure-pass!"})
        self.assertEqual(ok.status_code, 200)


class FakeResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status, self._headers, self._body = status, headers or {}, body

    def getheader(self, name, default=None):
        return self._headers.get(name, default)

    def read(self, n=-1):
        return self._body[:n] if n >= 0 else self._body


class SSRFTests(SimpleTestCase):
    def test_rejects_unsafe_urls(self):
        for url in ["file:///etc/passwd", "ftp://example.com/x", "gopher://x", "http://localhost/", "http://127.0.0.1/",
                    "http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/", "http://169.254.169.254/latest/",
                    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://user:pw@example.com/",
                    "http://example.com:8080/", "http://[::1", "not a url", "//example.com/x", "http://foo.internal/"]:
            with self.subTest(url=url), self.assertRaises(UnsafeURLError):
                validate_url(url)

    def test_hostname_resolving_to_private_ip_rejected(self):
        with mock.patch("core.http._resolve", return_value=["93.184.216.34", "10.0.0.5"]):
            with self.assertRaises(UnsafeURLError):
                validate_url("https://evil-rebind.example/")

    def test_connects_to_vetted_ip_only(self):
        with mock.patch("core.http._resolve", return_value=["93.184.216.34"]), \
             mock.patch("core.http._request", return_value=(mock.Mock(), FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>ok</html>"))) as req:
            result = safe_fetch("https://shop.example/p?page=2", max_bytes=1000)
        self.assertEqual(result.body, b"<html>ok</html>")
        scheme, host, port, path, ip = req.call_args.args[:5]
        self.assertEqual((scheme, host, port, path, ip), ("https", "shop.example", 443, "/p?page=2", "93.184.216.34"))

    def test_redirect_into_internal_network_rejected(self):
        responses = [(mock.Mock(), FakeResponse(302, {"Location": "http://127.0.0.1/admin"}))]
        with mock.patch("core.http._resolve", return_value=["93.184.216.34"]), \
             mock.patch("core.http._request", side_effect=responses):
            with self.assertRaises(UnsafeURLError):
                safe_fetch("https://shop.example/", max_bytes=1000)
        responses = [(mock.Mock(), FakeResponse(301, {"Location": "file:///etc/passwd"}))]
        with mock.patch("core.http._resolve", return_value=["93.184.216.34"]), \
             mock.patch("core.http._request", side_effect=responses):
            with self.assertRaises(UnsafeURLError):
                safe_fetch("https://shop.example/", max_bytes=1000)

    def test_response_size_limit(self):
        with mock.patch("core.http._resolve", return_value=["93.184.216.34"]), \
             mock.patch("core.http._request", return_value=(mock.Mock(), FakeResponse(200, {}, b"x" * 2000))):
            with self.assertRaises(FetchError):
                safe_fetch("https://shop.example/", max_bytes=1000)

    def test_importer_and_image_download_refuse_local_files(self):
        with self.assertRaises(SourceUnavailableError):
            fetch_html("file:///etc/passwd")
        with self.assertRaises(SourceUnavailableError):
            fetch_html("http://127.0.0.1:8000/api/admin/")
        self.assertIsNone(download_image("file:///etc/passwd"))
        self.assertIsNone(download_image("http://169.254.169.254/latest/meta-data/"))

    def test_offsite_pagination_not_followed(self):
        page = '<html><a class="next page-numbers" href="https://attacker.example/x">next</a></html>'
        with mock.patch("products.services.importer.fetch_html", return_value=page) as fetch:
            pages = list(iterate_listing_pages("https://shop.example/shop/"))
        self.assertEqual(len(pages), 1)
        fetch.assert_called_once_with("https://shop.example/shop/")


class ProductionSettingsTests(SimpleTestCase):
    GOOD = {
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "x" * 50,
        "DJANGO_ALLOWED_HOSTS": "api.example.com",
        "POSTGRES_PASSWORD": "secret",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "DB_ENGINE": "postgresql",
    }

    def boot(self, **overrides):
        env = {k: v for k, v in os.environ.items() if k not in self.GOOD and k != "DJANGO_ENV"}
        env.update({**self.GOOD, **overrides})
        env = {k: v for k, v in env.items() if v is not None}
        code = "import django; django.setup(); print('BOOT_OK')"
        # All variables are set explicitly; python-dotenv never overrides existing ones.
        return subprocess.run([sys.executable, "-c", code], cwd=BASE_DIR, env={**env, "PYTHONPATH": str(BASE_DIR)},
                              capture_output=True, text=True, timeout=60)

    def assert_refused(self, needle, **overrides):
        res = self.boot(**overrides)
        self.assertNotEqual(res.returncode, 0, res.stdout)
        self.assertIn("ImproperlyConfigured", res.stderr)
        self.assertIn(needle, res.stderr)

    def test_valid_production_config_boots(self):
        res = self.boot()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("BOOT_OK", res.stdout)

    def test_missing_required_settings_fail_safely(self):
        self.assert_refused("DJANGO_ALLOWED_HOSTS", DJANGO_ALLOWED_HOSTS="")
        self.assert_refused("POSTGRES_PASSWORD", POSTGRES_PASSWORD="")
        self.assert_refused("REDIS_URL", REDIS_URL="")
        self.assert_refused("DJANGO_SECRET_KEY", DJANGO_SECRET_KEY="django-insecure-short")
        self.assert_refused("PostgreSQL", DB_ENGINE="sqlite")
        self.assert_refused("must not contain", DJANGO_ALLOWED_HOSTS="*")

    def test_dev_settings_refuse_production_environment(self):
        res = self.boot(DJANGO_SETTINGS_MODULE="config.settings.dev", DJANGO_ENV="production")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("config.settings.prod", res.stderr)
