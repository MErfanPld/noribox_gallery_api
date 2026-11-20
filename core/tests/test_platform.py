"""CMS, reports, store settings, product images, schema protection, job locking, migrations."""
import threading

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from io import StringIO

from cms.models import Page, StoreSettings
from core.locks import exclusive
from core.testutils import client_for, make_product, make_staff, make_user, png_file


class CMSAndSettingsTests(TestCase):
    def test_page_publication_flow(self):
        editor = client_for(make_staff("cms.add_page", "cms.change_page", "cms.view_page"))
        res = editor.post("/api/admin/cms/pages/", {"title": "About us", "body": "Hi"})
        self.assertEqual(res.status_code, 201, res.data)
        slug = res.data["slug"]
        self.assertEqual(client_for().get(f"/api/v1/pages/{slug}/").status_code, 404)  # draft
        editor.patch(f"/api/admin/cms/pages/{res.data['id']}/", {"status": "published"})
        self.assertEqual(client_for().get(f"/api/v1/pages/{slug}/").data["title"], "About us")
        self.assertEqual(client_for(make_staff()).post("/api/admin/cms/pages/", {"title": "x"}).status_code, 403)

    def test_faq_banner_and_store_settings(self):
        admin = client_for(make_user(is_staff=True, is_superuser=True))
        admin.post("/api/admin/cms/faqs/", {"question": "Q?", "answer": "A", "group": "shipping"})
        self.assertEqual(client_for().get("/api/v1/faqs/", {"group": "shipping"}).data[0]["question"], "Q?")
        res = admin.post("/api/admin/cms/banners/", {"title": "B", "position": "home_top", "is_active": True, "image": png_file("b.png")}, format="multipart")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(len(client_for().get("/api/v1/banners/", {"position": "home_top"}).data), 1)
        res = admin.patch("/api/admin/store-settings/", {"whatsapp_number": "989121234567", "admin_notification_phones": "09120000009"})
        self.assertEqual(res.status_code, 200)
        public = client_for().get("/api/v1/store-settings/").data
        self.assertEqual(public["whatsapp_number"], "989121234567")
        self.assertNotIn("admin_notification_phones", public)
        self.assertEqual(StoreSettings.objects.count(), 1)

    def test_reports_summary(self):
        self.assertEqual(client_for(make_staff()).get("/api/admin/reports/summary/").status_code, 403)
        res = client_for(make_staff("commerce.view_order")).get("/api/admin/reports/summary/", {"days": 7})
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data["period_days"], res.data["revenue"]), (7, 0))
        self.assertIn("inventory", res.data)


class ProductImageUploadTests(TestCase):
    def test_upload_and_validation(self):
        p = make_product()
        c = client_for(make_staff("catalog.add_productimage", "catalog.view_productimage"))
        res = c.post("/api/admin/product-images/", {"product": p.pk, "image": png_file("a.png"), "is_primary": True}, format="multipart")
        self.assertEqual(res.status_code, 201, res.data)
        detail = client_for().get(f"/api/v1/products/{p.slug}/").data
        self.assertTrue(detail["images"][0]["image"].startswith("http://testserver/media/products/"))
        from django.core.files.uploadedfile import SimpleUploadedFile

        bad = SimpleUploadedFile("x.png", b"<?php echo 1; ?>", content_type="image/png")
        self.assertEqual(c.post("/api/admin/product-images/", {"product": p.pk, "image": bad}, format="multipart").status_code, 400)


class SchemaProtectionTests(TestCase):
    def test_public_schema_excludes_admin_and_admin_schema_requires_staff(self):
        public = client_for().get("/api/schema/", HTTP_ACCEPT="application/json").json()
        self.assertTrue(public["paths"])
        self.assertFalse([p for p in public["paths"] if p.startswith("/api/admin/")])
        self.assertEqual(client_for().get("/api/admin/schema/").status_code, 401)
        self.assertEqual(client_for(make_user()).get("/api/admin/schema/").status_code, 403)
        res = client_for(make_staff()).get("/api/admin/schema/", HTTP_ACCEPT="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(all(p.startswith("/api/admin/") for p in res.json()["paths"]))


class ScheduledJobLockTests(TestCase):
    def test_overlapping_run_is_skipped(self):
        if connection.vendor != "postgresql":
            self.skipTest("advisory locks are PostgreSQL-only")
        held, release = threading.Event(), threading.Event()

        def holder():
            with exclusive("run_scheduled_jobs") as ok:
                assert ok
                held.set()
                release.wait(10)
            connection.close()

        t = threading.Thread(target=holder)
        t.start()
        held.wait(10)
        out = StringIO()
        call_command("run_scheduled_jobs", stdout=out)
        release.set()
        t.join()
        self.assertIn("skipping", out.getvalue())
        out = StringIO()
        call_command("run_scheduled_jobs", stdout=out)
        self.assertIn("Processed", out.getvalue())


class MigrationTests(TransactionTestCase):
    def test_no_missing_migrations(self):
        call_command("makemigrations", "--check", "--dry-run", stdout=StringIO())

    def test_prototype_product_rename_preserves_rows(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("products", "0002_product_categories")])
        old_apps = executor.loader.project_state([("products", "0002_product_categories")]).apps
        OldProduct = old_apps.get_model("products", "Product")
        OldProduct.objects.create(source_site="m", source_product_id="42", name="Legacy", product_url="https://x.test/p")
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        from products.models import SourceProduct

        sp = SourceProduct.objects.get(source_product_id="42")
        self.assertEqual((sp.name, sp.review_status), ("Legacy", "pending"))
