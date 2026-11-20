import re
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import OTPCode, User
from core.testutils import client_for, make_user, reset_sms
from notifications.models import SMSLog


def last_code(outbox):
    return re.search(r"(\d{6})", outbox[-1]["message"]).group(1)


class OTPTests(TestCase):
    def setUp(self):
        self.sms = reset_sms()
        self.c = client_for()

    def request(self, phone="09121234567", purpose="login"):
        return self.c.post("/api/v1/auth/otp/request/", {"phone": phone, "purpose": purpose})

    def test_otp_login_creates_user_and_issues_tokens(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.request("+989121234567").status_code, 202)
        code = last_code(self.sms.outbox)
        otp = OTPCode.objects.get()
        self.assertNotEqual(otp.code_hash, code)  # hashed
        res = self.c.post("/api/v1/auth/otp/login/", {"phone": "09121234567", "code": code})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("access", res.data)
        self.assertTrue(User.objects.get(phone="09121234567").phone_verified_at)
        # Code is single-use
        res = self.c.post("/api/v1/auth/otp/login/", {"phone": "09121234567", "code": code})
        self.assertEqual(res.status_code, 400)
        # OTP never stored in plaintext in logs
        self.assertFalse(SMSLog.objects.filter(message__contains=code).exists())

    def test_attempt_limit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.request()
        code = last_code(self.sms.outbox)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.c.post("/api/v1/auth/otp/login/", {"phone": "09121234567", "code": wrong}).status_code, 400)
        self.assertEqual(OTPCode.objects.get().attempts, 5)
        res = self.c.post("/api/v1/auth/otp/login/", {"phone": "09121234567", "code": code})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["code"], "otp_invalid")

    def test_expired_code(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.request()
        code = last_code(self.sms.outbox)
        OTPCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.c.post("/api/v1/auth/otp/login/", {"phone": "09121234567", "code": code}).status_code, 400)

    def test_resend_cooldown_and_invalidation(self):
        self.request()
        res = self.request()
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["error"]["code"], "otp_cooldown")
        OTPCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.assertEqual(self.request().status_code, 202)
        self.assertEqual(OTPCode.objects.filter(invalidated_at__isnull=True).count(), 1)

    @override_settings(OTP_MAX_PER_HOUR=2, OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_hourly_limit(self):
        self.request()
        self.request()
        self.assertEqual(self.request().status_code, 429)

    def test_invalid_phone(self):
        self.assertEqual(self.request("12345").status_code, 400)

    def test_register_then_password_login(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.request("09351234567", "register")
        code = last_code(self.sms.outbox)
        res = self.c.post("/api/v1/auth/register/", {"phone": "09351234567", "code": code, "password": "Very-Strong-123", "first_name": "Sara"})
        self.assertEqual(res.status_code, 201, res.data)
        ok = self.c.post("/api/v1/auth/login/", {"phone": "09351234567", "password": "Very-Strong-123"})
        self.assertEqual(ok.status_code, 200)
        bad = self.c.post("/api/v1/auth/login/", {"phone": "09351234567", "password": "nope"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.request("09351234567", "register").status_code, 400)  # already registered

    def test_password_reset(self):
        make_user("09125550000")
        OTPCode.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True):
            self.request("09125550000", "reset_password")
        code = last_code(self.sms.outbox)
        res = self.c.post("/api/v1/auth/password/reset/", {"phone": "09125550000", "code": code, "new_password": "Another-Strong-9"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(phone="09125550000").check_password("Another-Strong-9"))
        # unknown phone gets the same response but no SMS
        before = len(self.sms.outbox)
        self.assertEqual(self.request("09129999999", "reset_password").status_code, 202)
        self.assertEqual(len(self.sms.outbox), before)

    def test_refresh_and_logout_blacklists(self):
        user = make_user()
        tokens = self.c.post("/api/v1/auth/login/", {"phone": user.phone, "password": "S3cure-pass!"}).data
        self.c.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(self.c.get("/api/v1/account/me/").status_code, 200)
        self.assertEqual(self.c.post("/api/v1/auth/logout/", {"refresh": tokens["refresh"]}).status_code, 205)
        self.assertEqual(self.c.post("/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}).status_code, 401)


class AddressTests(TestCase):
    def test_addresses_are_scoped_and_default_unique(self):
        a, b = make_user(), make_user()
        ca = client_for(a)
        payload = {"recipient_name": "A", "recipient_phone": "09121112233", "province": "T", "city": "T",
                   "address_line": "x", "postal_code": "1234567890"}
        first = ca.post("/api/v1/account/addresses/", payload).data
        self.assertTrue(first["is_default"])
        second = ca.post("/api/v1/account/addresses/", {**payload, "is_default": True}).data
        self.assertTrue(second["is_default"])
        self.assertEqual(a.addresses.filter(is_default=True).count(), 1)
        self.assertEqual(client_for(b).get(f"/api/v1/account/addresses/{first['id']}/").status_code, 404)
        self.assertEqual(ca.post("/api/v1/account/addresses/", {**payload, "postal_code": "12"}).status_code, 400)
