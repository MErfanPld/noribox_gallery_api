"""Regression tests for the audit findings in commerce / payments / inventory."""
import os
import threading
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from catalog.models import Category, Product, ProductImage
from commerce.models import Order
from commerce.services import add_to_cart, cancel_stale_unpaid_orders, create_order
from core.exceptions import ConflictError
from core.testutils import client_for, make_address, make_product, make_staff, make_user, png_file, reset_sms, setup_store
from inventory.models import AvailabilityState, Reservation, VariantAvailability
from inventory.services import expire_reservations, set_availability
from payments.models import Payment, PaymentReceipt
from payments.services import approve_receipt, retry_unreserved_receipts, upload_receipt


def place(user, variants, method):
    for v in variants:
        add_to_cart(user, v.pk)
    return create_order(user, user.addresses.first() or make_address(user), method)


def state(v):
    return VariantAvailability.objects.get(variant=v)


def private_files():
    root = settings.PRIVATE_MEDIA_ROOT
    return {os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs}


class Base(TestCase):
    def setUp(self):
        reset_sms()
        self.method = setup_store()
        self.user = make_user()
        make_address(self.user)
        self.p = make_product(n_variants=2)
        self.v1, self.v2 = self.p.variants.all()
        self.finance = make_staff("payments.review_payment", "payments.view_paymentreceipt")

    def order(self):
        return place(self.user, [self.v1, self.v2], self.method)


class ScheduledCancellationTests(Base):
    def age(self, order, hours=100):
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=hours))

    def test_cron_never_cancels_after_receipt_upload(self):
        order = self.order()
        self.age(order)
        upload_receipt(order, self.user, png_file())
        self.assertEqual(cancel_stale_unpaid_orders(), [])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_SUBMITTED)
        self.assertEqual(state(self.v1).state, "reserved")

    def test_cron_cancels_only_eligible_unpaid(self):
        stale, fresh = self.order(), place(self.user, [make_product().variants.first()], self.method)
        self.age(stale)
        self.assertEqual(cancel_stale_unpaid_orders(), [stale.number])
        stale.refresh_from_db(), fresh.refresh_from_db()
        self.assertEqual((stale.status, fresh.status), ("cancelled", "awaiting_payment"))

    def test_status_rechecked_under_lock(self):
        order = self.order()
        self.age(order)
        real_filter = Order.objects.filter

        def racing_filter(*args, **kwargs):
            # Simulate the customer's upload committing between the candidate
            # query and the lock: the row is now payment_submitted.
            if kwargs.get("status") == Order.Status.AWAITING_PAYMENT and "created_at__lt" in kwargs:
                qs = real_filter(*args, **kwargs)
                ids = list(qs.values_list("pk", flat=True))
                Order.objects.filter(pk__in=ids).update(status=Order.Status.PAYMENT_SUBMITTED)
                return real_filter(pk__in=ids)
            return real_filter(*args, **kwargs)

        with mock.patch.object(Order.objects, "filter", side_effect=racing_filter):
            self.assertEqual(cancel_stale_unpaid_orders(), [])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_SUBMITTED)


class AdminOverrideTests(Base):
    def test_admin_unavailable_variant_cannot_be_sold_by_approval(self):
        order = self.order()
        receipt = upload_receipt(order, self.user, png_file())
        set_availability(self.v1, AvailabilityState.UNAVAILABLE, note="damaged")
        res = client_for(self.finance).post(f"/api/admin/receipts/{receipt.pk}/approve/")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["error"]["code"], "admin_marked_unavailable")
        self.assertEqual(res.data["error"]["details"]["admin_unavailable_variant_ids"], [self.v1.pk])
        self.assertEqual((state(self.v1).state, state(self.v1).sold), ("unavailable", False))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_SUBMITTED)
        self.assertFalse(Reservation.objects.filter(status="converted").exists())
        # Rejection still works and keeps the admin's decision.
        client_for(self.finance).post(f"/api/admin/receipts/{receipt.pk}/reject/", {"reason": "item damaged"})
        self.assertEqual(state(self.v1).state, "unavailable")
        self.assertEqual(state(self.v2).state, "available")


class ReservationRecoveryTests(Base):
    def test_crashed_reservation_keeps_receipt_and_is_retried_idempotently(self):
        order = self.order()
        with mock.patch("payments.services.reserve_for_order", side_effect=RuntimeError("worker died")):
            receipt = upload_receipt(order, self.user, png_file())
        receipt.refresh_from_db()
        self.assertEqual(receipt.reservation_status, "not_attempted")
        self.assertTrue(receipt.file.storage.exists(receipt.file.name))
        self.assertEqual(state(self.v1).state, "available")
        self.assertEqual(retry_unreserved_receipts(min_age_seconds=0), 1)
        receipt.refresh_from_db()
        self.assertEqual(receipt.reservation_status, "reserved")
        retry_unreserved_receipts(min_age_seconds=0)  # nothing left; idempotent
        from payments.services import attempt_reservation

        attempt_reservation(receipt)  # explicit re-run is also safe
        self.assertEqual(Reservation.objects.filter(order=order, status="active").count(), 2)

    def test_failed_reservation_does_not_delete_receipt(self):
        order = self.order()
        other = make_user()
        make_address(other)
        upload_receipt(place(other, [self.v1], self.method), other, png_file())
        receipt = upload_receipt(order, self.user, png_file())
        self.assertEqual(receipt.reservation_status, "conflict")
        self.assertTrue(receipt.file.storage.exists(receipt.file.name))
        self.assertEqual(PaymentReceipt.objects.filter(pk=receipt.pk).count(), 1)

    def test_rollback_removes_orphan_receipt_file(self):
        order = self.order()
        before = private_files()
        with mock.patch("payments.services.set_status", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                upload_receipt(order, self.user, png_file())
        self.assertEqual(private_files(), before)
        self.assertFalse(PaymentReceipt.objects.exists())

    def test_expiry_does_not_flag_paid_orders(self):
        order = self.order()
        receipt = upload_receipt(order, self.user, png_file())
        approve_receipt(receipt, self.finance)
        Reservation.objects.update(expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(expire_reservations(), set())
        order.refresh_from_db()
        self.assertFalse(order.needs_review)


class CancellationAndRefundTests(Base):
    def test_admin_cancel_payment_submitted_updates_receipt_and_payment(self):
        order = self.order()
        receipt = upload_receipt(order, self.user, png_file())
        mgr = client_for(make_staff("commerce.manage_order_status", "commerce.view_order"))
        self.assertEqual(mgr.post(f"/api/admin/orders/{order.pk}/transition/", {"status": "cancelled"}).status_code, 200)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "cancelled")
        self.assertEqual(order.payments.get().status, Payment.Status.CANCELLED)
        self.assertEqual(state(self.v1).state, "available")
        order.refresh_from_db()
        self.assertEqual(order.refund_status, "none")
        with self.assertRaises(ConflictError):
            approve_receipt(receipt, self.finance)

    def test_cancel_paid_order_requires_recorded_refund(self):
        order = self.order()
        approve_receipt(upload_receipt(order, self.user, png_file()), self.finance)
        mgr = make_staff("commerce.manage_order_status", "commerce.view_order")
        client_for(mgr).post(f"/api/admin/orders/{order.pk}/transition/", {"status": "cancelled"})
        order.refresh_from_db()
        self.assertEqual((order.status, order.refund_status), ("cancelled", "pending"))
        self.assertEqual(order.payments.get().status, Payment.Status.APPROVED)  # money was received
        av = state(self.v1)
        self.assertEqual((av.state, av.sold), ("unavailable", False))  # restockable by admin decision
        self.assertEqual(client_for(mgr).post(f"/api/admin/orders/{order.pk}/record-refund/", {"reference": "R1"}).status_code, 403)
        refunder = client_for(make_staff("commerce.record_refund", "commerce.view_order"))
        self.assertEqual(refunder.post(f"/api/admin/orders/{order.pk}/record-refund/", {}).status_code, 400)
        res = refunder.post(f"/api/admin/orders/{order.pk}/record-refund/", {"reference": "TRX-991"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data["refund_status"], res.data["refund_reference"]), ("refunded", "TRX-991"))
        self.assertEqual(refunder.post(f"/api/admin/orders/{order.pk}/record-refund/", {"reference": "x"}).status_code, 409)
        set_availability(self.v1, AvailabilityState.AVAILABLE)  # restock allowed

    def test_order_item_image_url_is_renderable(self):
        ProductImage.objects.create(product=self.p, image=png_file("p.png"), is_primary=True)
        order = self.order()
        data = client_for(self.user).get(f"/api/v1/orders/{order.number}/").data
        self.assertTrue(data["items"][0]["image_url"].startswith("http://testserver/media/products/"))
        order.items.update(image_url="products/legacy.png")  # pre-fix rows
        data = client_for(self.user).get(f"/api/v1/orders/{order.number}/").data
        self.assertEqual(data["items"][0]["image_url"], "http://testserver/media/products/legacy.png")


class SafeDeletionTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = client_for(make_user(is_staff=True, is_superuser=True))

    def test_product_with_reservation_returns_409(self):
        upload_receipt(self.order(), self.user, png_file())
        res = self.admin.delete(f"/api/admin/products/{self.p.pk}/")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["error"]["code"], "protected")
        self.assertEqual(res.data["error"]["details"]["safe_alternative"], "archive")
        self.assertTrue(Product.objects.filter(pk=self.p.pk).exists())
        self.assertEqual(self.admin.post(f"/api/admin/products/{self.p.pk}/archive/").data["status"], "archived")

    def test_unused_product_can_be_deleted(self):
        p = make_product()
        self.assertEqual(self.admin.delete(f"/api/admin/products/{p.pk}/").status_code, 204)

    def test_category_with_dependencies_returns_409(self):
        parent = Category.objects.create(name="Parent")
        Category.objects.create(name="Child", parent=parent)
        res = self.admin.delete(f"/api/admin/categories/{parent.pk}/")
        self.assertEqual(res.status_code, 409)
        self.assertIn("Categories", res.data["error"]["details"]["referenced_by"])
        used = Category.objects.create(name="Used")
        make_product(category=used)
        self.assertEqual(self.admin.delete(f"/api/admin/categories/{used.pk}/").status_code, 409)
        self.assertEqual(self.admin.delete(f"/api/admin/categories/{Category.objects.create(name='Empty').pk}/").status_code, 204)


class CronUploadRaceTests(TransactionTestCase):
    """Real concurrency: receipt upload vs scheduled cancellation on PostgreSQL."""

    def test_upload_and_cron_cannot_invalidate_each_other(self):
        if connection.vendor != "postgresql":
            self.skipTest("needs row locks")
        method = setup_store()
        for _ in range(5):
            user = make_user()
            make_address(user)
            order = place(user, [make_product().variants.first()], method)
            Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=100))
            barrier, outcome = threading.Barrier(2), {}

            def do_upload():
                barrier.wait()
                try:
                    upload_receipt(order, user, png_file())
                    outcome["upload"] = "ok"
                except ConflictError:
                    outcome["upload"] = "rejected"
                finally:
                    connection.close()

            def do_cron():
                barrier.wait()
                outcome["cron"] = cancel_stale_unpaid_orders()
                connection.close()

            threads = [threading.Thread(target=do_upload), threading.Thread(target=do_cron)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            order.refresh_from_db()
            receipts = PaymentReceipt.objects.filter(payment__order=order)
            if outcome["upload"] == "ok":
                self.assertEqual(order.status, "payment_submitted")
                self.assertEqual(outcome["cron"], [])
                self.assertEqual(receipts.count(), 1)
            else:
                self.assertEqual(order.status, "cancelled")
                self.assertFalse(receipts.exists())
