import re
from unittest import mock

from django.db import IntegrityError
from django.test import TestCase
from rest_framework.throttling import SimpleRateThrottle

from accounts.models import User
from commerce.models import Cart
from commerce.services import get_cart
from core.testutils import client_for, make_user, reset_sms


class TokenRevocationTests(TestCase):
    def setUp(self):
        SimpleRateThrottle.cache.clear()
        self.user = make_user("09126660000")
        self.c = client_for()

    def login(self, password="S3cure-pass!"):
        return self.c.post("/api/v1/auth/login/", {"phone": self.user.phone, "password": password}).data

    def bearer(self, access):
        c = client_for()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return c

    def test_password_change_revokes_old_tokens(self):
        old = self.login()
        res = self.bearer(old["access"]).post("/api/v1/account/password/", {"current_password": "S3cure-pass!", "new_password": "Brand-New-Pass-77"})
        self.assertEqual(res.status_code, 200, res.data)
        new = res.data
        self.assertEqual(self.c.post("/api/v1/auth/token/refresh/", {"refresh": old["refresh"]}).status_code, 401)
        self.assertEqual(self.bearer(old["access"]).get("/api/v1/account/me/").status_code, 401)
        self.assertEqual(self.bearer(new["access"]).get("/api/v1/account/me/").status_code, 200)
        self.assertEqual(self.c.post("/api/v1/auth/token/refresh/", {"refresh": new["refresh"]}).status_code, 200)

    def test_password_reset_revokes_old_refresh(self):
        sms = reset_sms()
        old = self.login()
        with self.captureOnCommitCallbacks(execute=True):
            self.c.post("/api/v1/auth/otp/request/", {"phone": self.user.phone, "purpose": "reset_password"})
        code = re.search(r"(\d{6})", sms.outbox[-1]["message"]).group(1)
        res = self.c.post("/api/v1/auth/password/reset/", {"phone": self.user.phone, "code": code, "new_password": "Reset-Pass-4242"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.c.post("/api/v1/auth/token/refresh/", {"refresh": old["refresh"]}).status_code, 401)
        self.assertEqual(self.bearer(old["access"]).get("/api/v1/account/me/").status_code, 401)

    def test_concurrent_registration_returns_conflict(self):
        def racing_verify(phone, purpose, code):
            User.objects.create_user(phone=phone)  # the other request wins the race

        # Both requests passed validation; the loser hits the DB unique constraint.
        with mock.patch("accounts.views.verify_otp", side_effect=racing_verify), \
             mock.patch.object(User, "full_clean", lambda self, *a, **k: None):
            res = self.c.post("/api/v1/auth/register/", {"phone": "09125554444", "code": "123456", "password": "Very-Strong-123"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["error"]["code"], "phone_registered")

    def test_concurrent_cart_creation(self):
        Cart.objects.create(user=self.user)  # the concurrent request committed first

        with mock.patch.object(Cart.objects, "get_or_create", side_effect=IntegrityError("duplicate")):
            cart = get_cart(self.user)
        self.assertEqual(cart, Cart.objects.get(user=self.user))
