"""Admin CRM endpoints: orders, notes, payment review, customers, dashboard, me, products, audit."""
from django.test import TestCase

from catalog.models import Category
from commerce.models import Order
from commerce.tests.test_checkout_flow import place_order, upload
from core.models import AuditLog
from core.testutils import client_for, make_product, make_staff, make_user, reset_sms, setup_store
from payments.models import PaymentReceipt

ORDER_STAFF = ("commerce.view_order", "commerce.change_order", "commerce.manage_order_status",
               "payments.view_paymentreceipt", "payments.review_payment")


class AdminOrderTests(TestCase):
    def setUp(self):
        reset_sms()
        self.method = setup_store()
        self.customer = make_user(first_name="مریم", last_name="احمدی")
        self.product = make_product()
        self.order = place_order(self.customer, [self.product.variants.first()], self.method)
        self.staff = make_staff(*ORDER_STAFF)
        self.api = client_for(self.staff)

    def test_list_search_filters_and_detail(self):
        res = self.api.get("/api/admin/orders/", {"search": "احمدی"}).json()
        self.assertEqual(res["count"], 1)
        row = res["results"][0]
        self.assertEqual((row["number"], row["item_count"], row["customer_name"]), (self.order.number, 1, "مریم احمدی"))
        self.assertEqual(self.api.get("/api/admin/orders/", {"status": "cancelled"}).json()["count"], 0)
        self.assertEqual(self.api.get("/api/admin/orders/", {"created_after": "2999-01-01T00:00:00Z"}).json()["count"], 0)
        self.assertEqual(self.api.get("/api/admin/orders/", {"search": self.order.number}).json()["count"], 1)
        detail = self.api.get(f"/api/admin/orders/{self.order.pk}/").json()
        self.assertEqual(detail["allowed_transitions"], ["cancelled"])
        self.assertEqual(detail["recipient_name"], self.order.recipient_name)

    def test_internal_notes_are_staff_only(self):
        res = self.api.post(f"/api/admin/orders/{self.order.pk}/notes/", {"body": "تماس گرفته شد"}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        detail = self.api.get(f"/api/admin/orders/{self.order.pk}/").json()
        self.assertEqual(detail["notes"][0]["body"], "تماس گرفته شد")
        customer_view = client_for(self.customer).get(f"/api/v1/orders/{self.order.number}/").json()
        self.assertNotIn("notes", customer_view)
        self.assertNotIn("admin_note", customer_view)
        viewer = make_staff("commerce.view_order")
        self.assertEqual(client_for(viewer).post(f"/api/admin/orders/{self.order.pk}/notes/", {"body": "x"},
                                                 format="json").status_code, 403)
        self.assertEqual(self.api.post(f"/api/admin/orders/{self.order.pk}/notes/", {"body": "  "},
                                       format="json").status_code, 400)

    def test_payment_approval_and_rejection_through_services(self):
        self.assertEqual(upload(client_for(self.customer), self.order).status_code, 201)
        receipt = PaymentReceipt.objects.get()
        detail = self.api.get(f"/api/admin/orders/{self.order.pk}/").json()
        self.assertIn("/api/admin/receipts/", detail["payment"]["receipts"][0]["file_url"])  # never public media
        res = self.api.post(f"/api/admin/receipts/{receipt.pk}/reject/", {"reason": "مبلغ ناقص"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "awaiting_payment")
        upload(client_for(self.customer), self.order)
        receipt2 = PaymentReceipt.objects.latest("id")
        res = self.api.post(f"/api/admin/receipts/{receipt2.pk}/approve/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        reviews = self.api.get(f"/api/admin/receipts/{receipt2.pk}/").json()["reviews"]
        self.assertEqual(reviews[0]["reviewer"], self.staff.phone)
        res = self.api.post(f"/api/admin/orders/{self.order.pk}/transition/", {"status": "delivered"}, format="json")
        self.assertEqual(res.status_code, 409)  # must go through processing/shipped

    def test_customer_cannot_reach_admin_orders(self):
        self.assertEqual(client_for(self.customer).get("/api/admin/orders/").status_code, 403)
        self.assertEqual(client_for(self.customer).get(f"/api/admin/orders/{self.order.pk}/").status_code, 403)


class CustomerCrmTests(TestCase):
    def setUp(self):
        self.method = setup_store()
        self.staff = make_staff("accounts.view_user", "accounts.change_user")
        self.api = client_for(self.staff)
        self.customer = make_user(first_name="علی")
        place_order(self.customer, [make_product(price=2_000_000).variants.first()], self.method)

    def test_list_detail_and_metrics(self):
        res = self.api.get("/api/admin/customers/", {"search": self.customer.phone}).json()
        row = res["results"][0]
        self.assertEqual((row["order_count"], row["paid_order_count"], row["paid_total"]), (1, 0, 0))  # unpaid ≠ revenue
        Order.objects.filter(user=self.customer).update(status="paid")
        row = self.api.get("/api/admin/customers/", {"search": self.customer.phone}).json()["results"][0]
        self.assertEqual(row["paid_total"], Order.objects.get(user=self.customer).total)
        detail = self.api.get(f"/api/admin/customers/{self.customer.pk}/").json()
        self.assertEqual(len(detail["addresses"]), 1)
        self.assertEqual(len(detail["recent_orders"]), 1)
        self.assertTrue(any(e["type"] == "registered" for e in detail["activity"]))
        for secret in ("password", "token_version", "otp"):
            self.assertNotIn(secret, detail)
        staff_ids = [c["id"] for c in self.api.get("/api/admin/customers/").json()["results"]]
        self.assertNotIn(self.staff.pk, staff_ids)

    def test_deactivate_revokes_tokens_and_is_audited(self):
        version = self.customer.token_version
        res = self.api.post(f"/api/admin/customers/{self.customer.pk}/set-active/", {"is_active": False, "reason": "spam"},
                            format="json")
        self.assertFalse(res.json()["is_active"])
        self.customer.refresh_from_db()
        self.assertGreater(self.customer.token_version, version)
        self.assertTrue(AuditLog.objects.filter(action="customer.deactivate", actor=self.staff).exists())
        viewer = make_staff("accounts.view_user")
        self.assertEqual(client_for(viewer).post(f"/api/admin/customers/{self.customer.pk}/set-active/",
                                                 {"is_active": True}, format="json").status_code, 403)


class DashboardMeProductsTests(TestCase):
    def test_dashboard_counts_real_data(self):
        method = setup_store()
        place_order(make_user(), [make_product().variants.first()], method)
        make_product(status="draft")
        api = client_for(make_staff("commerce.view_order"))
        data = api.get("/api/admin/dashboard/", {"days": 7}).json()
        self.assertEqual(data["orders"]["total"], 1)
        self.assertEqual(data["orders"]["pending"], 1)
        self.assertEqual(data["products"], {"published": 1, "draft": 1, "archived": 0})
        self.assertEqual(data["customers"]["total"], 1)
        self.assertEqual(data["sales"]["revenue"], 0)
        self.assertEqual(client_for(make_user()).get("/api/admin/dashboard/").status_code, 403)

    def test_me_lists_effective_permissions(self):
        staff = make_staff("catalog.view_product")
        me = client_for(staff).get("/api/admin/me/").json()
        self.assertEqual(me["permissions"], ["catalog.view_product"])
        self.assertEqual(client_for(make_user()).get("/api/admin/me/").status_code, 403)
        self.assertEqual(client_for().get("/api/admin/me/").status_code, 401)

    def test_product_admin_list_search_and_filters(self):
        cat = Category.objects.create(name="عطر", slug="perfume")
        p = make_product(name="Oud Royal", category=cat)
        make_product(name="Other", status="draft")
        api = client_for(make_staff("catalog.view_product", "catalog.add_product", "catalog.change_product",
                                    "catalog.publish_product"))
        rows = api.get("/api/admin/products/", {"search": "oud"}).json()["results"]
        self.assertEqual([(r["name"], r["available_count"], r["category_names"]) for r in rows], [("Oud Royal", 1, ["عطر"])])
        self.assertEqual(api.get("/api/admin/products/", {"status": "draft"}).json()["count"], 1)
        sku = p.variants.first().sku
        self.assertEqual(api.get("/api/admin/products/", {"search": sku}).json()["count"], 1)
        self.assertEqual(api.get("/api/admin/products/", {"availability": "reserved"}).json()["count"], 0)
        self.assertEqual(api.get("/api/admin/products/", {"imported": "true"}).json()["count"], 0)
        # Manual product creation → variant → publish → visible.
        res = api.post("/api/admin/products/", {"name": "New perfume", "category_links": [{"category": cat.pk}]}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        pid = res.json()["id"]
        self.assertEqual(api.post(f"/api/admin/products/{pid}/publish/").status_code, 400)  # no variant yet
        api_v = client_for(make_staff("catalog.add_productvariant"))
        self.assertEqual(api_v.post("/api/admin/variants/", {"product": pid, "sku": "NEW-1", "price": 1500000},
                                    format="json").status_code, 201)
        self.assertEqual(api.post(f"/api/admin/products/{pid}/publish/").json()["status"], "published")
        slugs = [r["slug"] for r in client_for().get("/api/v1/products/").json()["results"]]
        self.assertIn(res.json()["slug"], slugs)
        self.assertTrue(AuditLog.objects.filter(action="product.published").exists())
        audit = client_for(make_staff("core.view_auditlog")).get("/api/admin/audit-logs/").json()
        self.assertGreaterEqual(audit["count"], 1)
