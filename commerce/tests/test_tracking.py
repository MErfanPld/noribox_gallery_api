from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.throttling import SimpleRateThrottle
from unittest import mock

from commerce.models import Order
from commerce.services import add_to_cart, cancel_stale_unpaid_orders, create_order, payment_deadline
from core.testutils import client_for, make_address, make_product, make_staff, make_user, png_file, reset_sms, setup_store
from payments.services import reject_receipt, upload_receipt

FORBIDDEN = {"address_line", "postal_code", "recipient_phone", "plaque", "unit", "customer_note", "admin_note",
             "payment", "receipts", "user", "refund_status", "refund_reference", "review_reason", "history"}


class Base(TestCase):
    def setUp(self):
        reset_sms()
        SimpleRateThrottle.cache.clear()
        self.method = setup_store()
        self.user = make_user("09121110000")
        self.address = make_address(self.user)  # recipient Ali, 09121112233
        add_to_cart(self.user, make_product().variants.first().pk)
        self.order = create_order(self.user, self.address, self.method)

    def track(self, number=None, phone="09121110000", **extra):
        return client_for().post("/api/v1/orders/track/", {"number": number or self.order.number, "phone": phone}, **extra)


class TrackingTests(Base):
    def test_owner_and_recipient_phone_accepted(self):
        for phone in ["09121110000", "+989121110000", "09121112233"]:
            res = self.track(phone=phone)
            self.assertEqual(res.status_code, 200, (phone, res.data))
        res = self.track(number=self.order.number.lower())
        self.assertEqual(res.data["number"], self.order.number)

    def test_limited_output_and_masking(self):
        data = self.track().data
        self.assertFalse(FORBIDDEN & set(data), FORBIDDEN & set(data))
        self.assertEqual(data["recipient_name"], "A***")
        self.assertEqual((data["province"], data["city"], data["item_count"]), ("Tehran", "Tehran", 1))
        self.assertEqual(set(data["items"][0]), {"product_name", "variant_label", "image_url", "unit_price"})
        self.assertEqual(data["payment_status"], "awaiting_receipt")
        self.assertIsNotNone(data["payment_expires_at"])
        self.assertEqual(set(data["timeline"][0]), {"status", "at"})
        self.assertNotIn("09121112233", str(data))
        self.assertNotIn("Valiasr", str(data))

    def test_unknown_order_and_wrong_phone_are_identical(self):
        wrong = self.track(phone="09129999999")
        missing = self.track(number="NBXXXXXXXXXX")
        self.assertEqual((wrong.status_code, missing.status_code), (404, 404))
        self.assertEqual(wrong.data, missing.data)
        self.assertEqual(wrong.data["error"]["code"], "order_not_found")

    def test_invalid_input(self):
        self.assertEqual(self.track(phone="123").status_code, 400)
        self.assertEqual(client_for().post("/api/v1/orders/track/", {}).status_code, 400)

    @override_settings(ORDER_TRACK_MAX_FAILURES=3)
    def test_lockout_per_order_number(self):
        for _ in range(3):
            self.assertEqual(self.track(phone="09129999999").status_code, 404)
        res = self.track()  # correct phone, but locked
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["error"]["code"], "track_locked")

    def test_ip_throttle(self):
        rates = {**SimpleRateThrottle.THROTTLE_RATES, "order_track": "2/min"}
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
            codes = [self.track(phone="0912999999" + str(i), HTTP_X_FORWARDED_FOR=f"1.1.1.{i}").status_code for i in range(4)]
        self.assertEqual(codes, [404, 404, 429, 429])

    def test_ignores_authentication(self):
        c = client_for(make_staff())
        self.assertEqual(c.post("/api/v1/orders/track/", {"number": self.order.number, "phone": "09129999999"}).status_code, 404)


class PaymentDeadlineTests(Base):
    def test_exposed_on_order_detail_and_matches_service(self):
        data = client_for(self.user).get(f"/api/v1/orders/{self.order.number}/").data
        expected = self.order.created_at + timedelta(hours=48)
        self.assertEqual(data["payment_expires_at"], expected.astimezone(timezone.get_current_timezone()).isoformat())
        self.assertEqual(payment_deadline(self.order), expected)

    def test_null_outside_awaiting_payment(self):
        upload_receipt(self.order, self.user, png_file())
        self.order.refresh_from_db()
        self.assertIsNone(payment_deadline(self.order))
        self.assertIsNone(client_for(self.user).get(f"/api/v1/orders/{self.order.number}/").data["payment_expires_at"])

    def test_rejection_restarts_window_and_cron_uses_same_deadline(self):
        Order.objects.filter(pk=self.order.pk).update(created_at=timezone.now() - timedelta(hours=100))
        receipt = upload_receipt(self.order, self.user, png_file())
        reject_receipt(receipt, make_staff(), "unclear")
        self.order.refresh_from_db()
        deadline = payment_deadline(self.order)
        self.assertGreater(deadline, timezone.now())
        self.assertEqual(cancel_stale_unpaid_orders(), [])  # before the deadline
        self.assertEqual(cancel_stale_unpaid_orders(now=deadline + timedelta(seconds=1)), [self.order.number])

    def test_cron_cancels_exactly_after_deadline(self):
        deadline = payment_deadline(self.order)
        self.assertEqual(cancel_stale_unpaid_orders(now=deadline - timedelta(seconds=1)), [])
        self.assertEqual(cancel_stale_unpaid_orders(now=deadline + timedelta(seconds=1)), [self.order.number])
