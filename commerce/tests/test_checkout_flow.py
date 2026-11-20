import os
import threading
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from catalog.models import Product, ProductVariant
from commerce.models import CartItem, Order
from commerce.services import add_to_cart, create_order
from core.testutils import client_for, make_address, make_product, make_staff, make_user, png_file, reset_sms, setup_store
from inventory.models import AvailabilityState, Reservation, VariantAvailability
from inventory.services import expire_reservations, reserve_for_order, set_availability
from payments.models import Payment, PaymentReceipt


def place_order(user, variants, method):
    for v in variants:
        add_to_cart(user, v.pk)
    return create_order(user, user.addresses.first() or make_address(user), method)


def upload(client, order, file=None):
    return client.post(f"/api/v1/orders/{order.number}/receipts/", {"file": file or png_file(), "reference_number": "123"}, format="multipart")


def state(variant):
    return VariantAvailability.objects.get(variant=variant).state


class CartTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.c = client_for(self.user)
        self.p = make_product(n_variants=2)
        self.v1, self.v2 = self.p.variants.all()

    def test_requires_auth(self):
        self.assertEqual(client_for().get("/api/v1/cart/").status_code, 401)

    def test_add_is_idempotent_and_quantity_one(self):
        self.assertEqual(self.c.post("/api/v1/cart/items/", {"variant_id": self.v1.pk}).status_code, 201)
        res = self.c.post("/api/v1/cart/items/", {"variant_id": self.v1.pk})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["item_count"], 1)
        self.assertEqual(res.data["items"][0]["quantity"], 1)
        item = CartItem.objects.get()
        with self.assertRaises(IntegrityError):
            CartItem.objects.filter(pk=item.pk).update(quantity=2)

    def test_cannot_add_unavailable_or_draft(self):
        set_availability(self.v1, AvailabilityState.UNAVAILABLE)
        self.assertEqual(self.c.post("/api/v1/cart/items/", {"variant_id": self.v1.pk}).status_code, 409)
        draft = make_product(status=Product.Status.DRAFT)
        self.assertEqual(self.c.post("/api/v1/cart/items/", {"variant_id": draft.variants.first().pk}).status_code, 404)

    def test_remove_only_own_items(self):
        self.c.post("/api/v1/cart/items/", {"variant_id": self.v1.pk})
        item = CartItem.objects.get()
        self.assertEqual(client_for(make_user()).delete(f"/api/v1/cart/items/{item.pk}/").status_code, 404)
        self.assertEqual(self.c.delete(f"/api/v1/cart/items/{item.pk}/").status_code, 204)


class OrderAndPaymentFlowTests(TestCase):
    def setUp(self):
        self.sms = reset_sms()
        self.method = setup_store()
        self.user = make_user()
        make_address(self.user)
        self.c = client_for(self.user)
        self.p = make_product(price=2_000_000, n_variants=2)
        self.v1, self.v2 = self.p.variants.all()
        self.finance = make_staff("payments.review_payment", "payments.view_paymentreceipt")
        self.fc = client_for(self.finance)

    def create_via_api(self):
        self.c.post("/api/v1/cart/items/", {"variant_id": self.v1.pk})
        self.c.post("/api/v1/cart/items/", {"variant_id": self.v2.pk})
        res = self.c.post("/api/v1/orders/", {"address_id": self.user.addresses.first().pk, "shipping_method_id": self.method.pk})
        self.assertEqual(res.status_code, 201, res.data)
        return Order.objects.get(number=res.data["number"])

    def test_order_creation_snapshots_totals_and_no_reservation(self):
        order = self.create_via_api()
        self.assertEqual(order.subtotal, 2_000_000 + 2_100_000)
        self.assertEqual(order.total, order.subtotal + 50_000)
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertFalse(Reservation.objects.exists())
        self.assertEqual(state(self.v1), "available")
        self.assertFalse(CartItem.objects.exists())
        self.assertEqual(order.payments.get().amount, order.total)
        # catalog change does not alter history
        ProductVariant.objects.filter(pk=self.v1.pk).update(price=1, label="changed")
        Product.objects.filter(pk=self.p.pk).update(name="Renamed")
        item = order.items.get(variant=self.v1)
        self.assertEqual((item.unit_price, item.product_name, item.variant_label), (2_000_000, self.p.name, "50ml"))
        detail = self.c.get(f"/api/v1/orders/{order.number}/").data
        self.assertEqual(detail["recipient_name"], "Ali")
        self.assertEqual(detail["total"], order.total)

    def test_empty_cart_and_foreign_address(self):
        res = self.c.post("/api/v1/orders/", {"address_id": self.user.addresses.first().pk, "shipping_method_id": self.method.pk})
        self.assertEqual(res.data["error"]["code"], "cart_empty")
        other = make_user()
        self.c.post("/api/v1/cart/items/", {"variant_id": self.v1.pk})
        res = self.c.post("/api/v1/orders/", {"address_id": make_address(other).pk, "shipping_method_id": self.method.pk})
        self.assertEqual(res.status_code, 404)

    def test_unavailable_item_blocks_order(self):
        self.c.post("/api/v1/cart/items/", {"variant_id": self.v1.pk})
        set_availability(self.v1, AvailabilityState.UNAVAILABLE)
        res = self.c.post("/api/v1/orders/", {"address_id": self.user.addresses.first().pk, "shipping_method_id": self.method.pk})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["error"]["details"]["unavailable_variant_ids"], [self.v1.pk])

    def test_full_happy_path_receipt_reserve_approve(self):
        order = self.create_via_api()
        with self.captureOnCommitCallbacks(execute=True):
            res = upload(self.c, order)
        self.assertEqual(res.status_code, 201, res.data)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_SUBMITTED)
        self.assertEqual(state(self.v1), "reserved")
        self.assertEqual(Reservation.objects.filter(order=order, status="active").count(), 2)
        self.assertTrue(any(m["phone"] == "09120000000" for m in self.sms.outbox))  # admin notified
        receipt = PaymentReceipt.objects.get()
        self.assertEqual(receipt.reservation_status, "reserved")
        # file is private: stored outside MEDIA_ROOT, no public URL
        self.assertTrue(receipt.file.path.startswith(os.path.abspath(settings.PRIVATE_MEDIA_ROOT)))
        self.assertFalse(receipt.file.path.startswith(os.path.abspath(settings.MEDIA_ROOT)))
        self.assertEqual(self.c.get(f"/api/v1/receipts/{receipt.pk}/file/").status_code, 200)
        self.assertEqual(client_for(make_user()).get(f"/api/v1/receipts/{receipt.pk}/file/").status_code, 404)
        # second upload while pending is refused
        self.assertEqual(upload(self.c, order).status_code, 409)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.fc.post(f"/api/admin/receipts/{receipt.pk}/approve/", {"reason": "ok"})
        self.assertEqual(res.status_code, 200, res.data)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(state(self.v1), "unavailable")
        self.assertTrue(VariantAvailability.objects.get(variant=self.v1).sold)
        self.assertEqual(Reservation.objects.filter(order=order, status="converted").count(), 2)
        self.assertEqual(order.payments.get().status, Payment.Status.APPROVED)
        self.assertTrue(any(m["phone"] == self.user.phone for m in self.sms.outbox))
        self.assertEqual(self.fc.post(f"/api/admin/receipts/{receipt.pk}/approve/").status_code, 409)
        statuses = list(order.status_history.values_list("to_status", flat=True))
        self.assertEqual(statuses, ["awaiting_payment", "payment_submitted", "paid"])

    def test_reject_releases_and_allows_resubmission(self):
        order = self.create_via_api()
        upload(self.c, order)
        receipt = PaymentReceipt.objects.get()
        self.assertEqual(self.fc.post(f"/api/admin/receipts/{receipt.pk}/reject/", {}).status_code, 400)  # reason required
        res = self.fc.post(f"/api/admin/receipts/{receipt.pk}/reject/", {"reason": "amount mismatch"})
        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(state(self.v1), "available")
        self.assertEqual(upload(self.c, order).status_code, 201)
        self.assertEqual(PaymentReceipt.objects.count(), 2)
        self.assertEqual(state(self.v1), "reserved")

    def test_reject_preserves_admin_unavailable(self):
        order = self.create_via_api()
        upload(self.c, order)
        set_availability(self.v1, AvailabilityState.UNAVAILABLE, note="damaged")
        self.fc.post(f"/api/admin/receipts/{PaymentReceipt.objects.get().pk}/reject/", {"reason": "x"})
        self.assertEqual(state(self.v1), "unavailable")
        self.assertEqual(state(self.v2), "available")

    def test_reservation_conflict_is_all_or_nothing_and_receipt_kept(self):
        order_a = self.create_via_api()
        other = make_user()
        make_address(other)
        order_b = place_order(other, [self.v2], self.method)
        upload(client_for(other), order_b)  # B reserves v2
        self.assertEqual(state(self.v2), "reserved")
        res = upload(self.c, order_a)  # A wants v1 + v2
        self.assertEqual(res.status_code, 201)
        order_a.refresh_from_db()
        receipt = PaymentReceipt.objects.get(payment__order=order_a)
        self.assertEqual(receipt.reservation_status, "conflict")
        self.assertTrue(order_a.needs_review)
        self.assertEqual(state(self.v1), "available")  # nothing partially reserved
        self.assertFalse(Reservation.objects.filter(order=order_a).exists())
        self.assertTrue(receipt.file.storage.exists(receipt.file.name))
        # approving A is refused while the conflict exists
        res = self.fc.post(f"/api/admin/receipts/{receipt.pk}/approve/")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["error"]["code"], "reservation_conflict")
        # once B is rejected, A can be approved (re-reserves atomically)
        self.fc.post(f"/api/admin/receipts/{PaymentReceipt.objects.get(payment__order=order_b).pk}/reject/", {"reason": "fake"})
        self.assertEqual(self.fc.post(f"/api/admin/receipts/{receipt.pk}/approve/").status_code, 200)
        self.assertEqual(state(self.v2), "unavailable")

    def test_invalid_receipt_files(self):
        order = self.create_via_api()
        fake = SimpleUploadedFile("r.png", b"not an image at all", content_type="image/png")
        self.assertEqual(upload(self.c, order, fake).data["error"]["code"], "invalid_file")
        exe = SimpleUploadedFile("r.exe", b"MZ....", content_type="application/octet-stream")
        self.assertEqual(upload(self.c, order, exe).status_code, 400)
        pdf_as_png = SimpleUploadedFile("r.png", b"%PDF-1.4 ...", content_type="image/png")
        self.assertEqual(upload(self.c, order, pdf_as_png).status_code, 400)
        with self.settings(RECEIPT_MAX_SIZE_BYTES=10):
            self.assertEqual(upload(self.c, order).status_code, 400)
        pdf = SimpleUploadedFile("r.pdf", b"%PDF-1.4\n%test\n", content_type="application/pdf")
        self.assertEqual(upload(self.c, order, pdf).status_code, 201)
        self.assertFalse(PaymentReceipt.objects.exclude(content_type="application/pdf").exists())

    def test_idor_on_orders_and_uploads(self):
        order = self.create_via_api()
        intruder = client_for(make_user())
        self.assertEqual(intruder.get(f"/api/v1/orders/{order.number}/").status_code, 404)
        self.assertEqual(upload(intruder, order).status_code, 404)
        self.assertEqual(intruder.post(f"/api/v1/orders/{order.number}/cancel/").status_code, 404)
        self.assertEqual(intruder.get("/api/v1/orders/").data["count"], 0)

    def test_customer_cancel_only_before_payment(self):
        order = self.create_via_api()
        upload(self.c, order)
        self.assertEqual(self.c.post(f"/api/v1/orders/{order.number}/cancel/").status_code, 409)
        order2 = place_order(self.user, [make_product().variants.first()], self.method)
        self.assertEqual(self.c.post(f"/api/v1/orders/{order2.number}/cancel/").status_code, 200)

    def test_admin_transitions_and_shipping(self):
        order = self.create_via_api()
        upload(self.c, order)
        self.fc.post(f"/api/admin/receipts/{PaymentReceipt.objects.get().pk}/approve/")
        mgr = client_for(make_staff("commerce.manage_order_status", "commerce.view_order", "shipping.change_shipment", "shipping.view_shipment"))
        self.assertEqual(mgr.post(f"/api/admin/orders/{order.pk}/transition/", {"status": "delivered"}).status_code, 409)
        self.assertEqual(mgr.post(f"/api/admin/orders/{order.pk}/transition/", {"status": "processing"}).status_code, 200)
        shipment = order.shipment
        self.assertEqual(mgr.patch(f"/api/admin/shipments/{shipment.pk}/", {"tracking_code": "TRK1", "status": "delivered"}).status_code, 200)
        shipment.refresh_from_db()
        self.assertEqual((shipment.tracking_code, shipment.status), ("TRK1", "preparing"))  # status read-only
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(mgr.post(f"/api/admin/orders/{order.pk}/transition/", {"status": "shipped"}).status_code, 200)
        self.assertTrue(any("TRK1" in m["message"] for m in self.sms.outbox))
        self.assertEqual(mgr.post(f"/api/admin/orders/{order.pk}/transition/", {"status": "delivered"}).status_code, 200)

    def test_expire_reservations(self):
        order = self.create_via_api()
        upload(self.c, order)
        Reservation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        expire_reservations()
        order.refresh_from_db()
        self.assertTrue(order.needs_review)
        self.assertEqual(state(self.v1), "available")
        self.assertEqual(Reservation.objects.filter(status="expired").count(), 2)

    def test_admin_cannot_make_reserved_variant_available(self):
        order = self.create_via_api()
        upload(self.c, order)
        inv = client_for(make_staff("inventory.manage_inventory", "inventory.view_variantavailability"))
        av = VariantAvailability.objects.get(variant=self.v1)
        res = inv.post(f"/api/admin/inventory/availability/{av.pk}/set-state/", {"state": "available"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(inv.post(f"/api/admin/inventory/availability/{av.pk}/set-state/", {"state": "reserved"}).status_code, 400)


class RBACTests(TestCase):
    def test_admin_api_requires_staff_and_permissions(self):
        self.assertEqual(client_for().get("/api/admin/orders/").status_code, 401)
        self.assertEqual(client_for(make_user()).get("/api/admin/orders/").status_code, 403)
        staff = make_staff()
        self.assertEqual(client_for(staff).get("/api/admin/orders/").status_code, 403)
        viewer = make_staff("commerce.view_order")
        self.assertEqual(client_for(viewer).get("/api/admin/orders/").status_code, 200)
        self.assertEqual(client_for(viewer).post("/api/admin/orders/1/transition/", {"status": "cancelled"}).status_code, 403)
        receipt_viewer = make_staff("payments.view_paymentreceipt")
        self.assertEqual(client_for(receipt_viewer).post("/api/admin/receipts/1/approve/").status_code, 403)

    def test_cannot_escalate_roles_without_permission(self):
        target = make_user()
        editor = make_staff("accounts.change_user", "accounts.view_user")
        res = client_for(editor).patch(f"/api/admin/users/{target.pk}/", {"is_staff": True})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(client_for(editor).patch(f"/api/admin/users/{target.pk}/", {"first_name": "X"}).status_code, 200)


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentReservationTests(TransactionTestCase):
    def test_only_one_concurrent_reservation_wins(self):
        method = setup_store()
        variant = make_product().variants.first()
        orders = []
        for _ in range(2):
            u = make_user()
            make_address(u)
            orders.append(place_order(u, [variant], method))
        results, barrier = [], threading.Barrier(2)

        def worker(order):
            barrier.wait()
            try:
                reserve_for_order(order)
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                results.append(type(exc).__name__)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(o,)) for o in orders]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ["ReservationConflict", "ok"])
        self.assertEqual(Reservation.objects.filter(variant=variant, status="active").count(), 1)

    def test_db_constraint_blocks_second_active_reservation(self):
        method = setup_store()
        variant = make_product().variants.first()
        u = make_user()
        make_address(u)
        order = place_order(u, [variant], method)
        Reservation.objects.create(variant=variant, order=order)
        with self.assertRaises(IntegrityError):
            Reservation.objects.create(variant=variant, order=order)
