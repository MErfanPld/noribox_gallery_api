from django.test import TestCase

from core.testutils import client_for
from core.utils import normalize_phone


class CoreTests(TestCase):
    def test_health(self):
        res = client_for().get("/api/health/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["database"])

    def test_error_format(self):
        res = client_for().get("/api/v1/products/does-not-exist/")
        self.assertEqual(res.status_code, 404)
        self.assertIn("code", res.json()["error"])

    def test_phone_normalization(self):
        for raw in ["09121234567", "+989121234567", "989121234567", "9121234567", "۰۹۱۲۱۲۳۴۵۶۷", "0912 123 4567"]:
            self.assertEqual(normalize_phone(raw), "09121234567")
        with self.assertRaises(ValueError):
            normalize_phone("02112345678")

    def test_schema_endpoint(self):
        self.assertEqual(client_for().get("/api/schema/").status_code, 200)
