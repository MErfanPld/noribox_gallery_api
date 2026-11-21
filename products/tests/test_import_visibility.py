"""End-to-end: import → staging → admin approval → public catalog visibility."""
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from catalog.models import Category, Product
from categories.models import Category as SourceCategory
from core.models import AuditLog
from core.testutils import client_for, make_staff, make_user
from products.models import ImportJob, SourceProduct
from products.services import importer
from products.services.importer import run_job, source_category_for_url

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "mehrdadgallery_shop_sample.html"
REVIEWER = ("products.view_sourceproduct", "products.review_sourceproduct", "catalog.add_product",
            "catalog.publish_product")


def priced():
    return SourceProduct.objects.exclude(regular_price=None).order_by("pk")


def public_slugs():
    res = client_for().get("/api/v1/products/", {"page_size": 100})
    assert res.status_code == 200, res.content
    return {p["slug"] for p in res.json()["results"]}


@mock.patch("products.services.review.download_image", return_value=None)
class ImportVisibilityTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="عطر", slug="perfume")
        self.staff = make_staff(*REVIEWER)
        self.api = client_for(self.staff)

    def import_fixture(self):
        out = StringIO()
        call_command("import_source_products", fixture=str(FIXTURE), stdout=out)
        return out.getvalue()

    def test_command_persists_staged_products_and_explains_review(self, _img):
        output = self.import_fixture()
        self.assertGreaterEqual(SourceProduct.objects.count(), 5)
        self.assertIn("STAGED for review", output)
        # Root cause of "imports not visible": staging never creates catalog products.
        self.assertFalse(Product.objects.exists())
        self.assertEqual(public_slugs(), set())

    def test_rerun_is_idempotent(self, _img):
        self.import_fixture()
        n = SourceProduct.objects.count()
        self.import_fixture()
        self.assertEqual(SourceProduct.objects.count(), n)

    def test_approve_publishes_and_product_appears_in_catalog_api(self, _img):
        self.import_fixture()
        sp = priced().filter(availability="in_stock").first()
        res = self.api.post(f"/api/admin/imports/source-products/{sp.pk}/approve/", {"category": self.category.pk}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        product = Product.objects.get(pk=res.json()["product_id"])
        self.assertEqual(product.status, "published")
        self.assertIn(product.slug, public_slugs())
        detail = client_for().get(f"/api/v1/products/{product.slug}/").json()
        self.assertEqual(detail["variants"][0]["availability"], "available")
        sp.refresh_from_db()
        self.assertEqual((sp.review_status, sp.reviewed_by), ("linked", self.staff))
        self.assertTrue(AuditLog.objects.filter(action="import.approve", actor=self.staff).exists())
        # Approving twice never creates a second product.
        self.api.post(f"/api/admin/imports/source-products/{sp.pk}/approve/", {}, format="json")
        self.assertEqual(Product.objects.count(), 1)

    def test_draft_stays_hidden_until_published(self, _img):
        self.import_fixture()
        sp = priced().first()
        res = self.api.post(f"/api/admin/imports/source-products/{sp.pk}/approve/",
                            {"category": self.category.pk, "publish": False}, format="json")
        self.assertEqual(res.json()["status"], "draft")
        self.assertEqual(public_slugs(), set())

    def test_publish_requires_category_and_rolls_back(self, _img):
        self.import_fixture()
        sp = priced().first()
        res = self.api.post(f"/api/admin/imports/source-products/{sp.pk}/approve/", {}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "category_required")
        self.assertFalse(Product.objects.exists())  # no orphan draft
        sp.refresh_from_db()
        self.assertEqual(sp.review_status, "pending")

    def test_mapped_source_category_is_assigned_automatically(self, _img):
        html = FIXTURE.read_text(encoding="utf-8")
        src = source_category_for_url("https://mehrdadgallery.com/product-category/men-perfume/page/2/", "mehrdadgallery")
        self.assertEqual(src.slug, "men-perfume")
        src.mapped_category = self.category
        src.save()
        with mock.patch.object(importer, "iterate_listing_pages", return_value=iter([html])):
            job = run_job(ImportJob.objects.create(source_site="mehrdadgallery", source_type="url",
                                                   source_url="https://mehrdadgallery.com/product-category/men-perfume/"))
        self.assertEqual(job.status, "completed")
        sp = priced().first()
        self.assertEqual(list(sp.categories.all()), [src])
        res = self.api.post(f"/api/admin/imports/source-products/{sp.pk}/approve/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Product.objects.get().primary_category, self.category)
        self.assertEqual(SourceCategory.objects.count(), 1)

    def test_reject_restore_and_bulk(self, _img):
        self.import_fixture()
        ids = list(priced().values_list("pk", flat=True)[:3])
        res = self.api.post(f"/api/admin/imports/source-products/{ids[0]}/reject/", {"reason": "fake"}, format="json")
        self.assertEqual(res.json()["review_status"], "ignored")
        res = self.api.post(f"/api/admin/imports/source-products/{ids[0]}/restore/")
        self.assertEqual(res.json()["review_status"], "pending")
        res = self.api.post("/api/admin/imports/source-products/bulk/",
                            {"ids": ids + [999999], "operation": "approve", "category": self.category.pk}, format="json")
        body = res.json()
        self.assertEqual((body["succeeded"], body["failed"]), (3, 1))
        self.assertEqual(Product.objects.published().count(), 3)
        summary = self.api.get("/api/admin/imports/source-products/summary/").json()
        self.assertEqual(summary["linked"], 3)

    def test_priceless_and_out_of_stock_sources(self, _img):
        self.import_fixture()
        no_price = SourceProduct.objects.filter(regular_price=None).first()
        res = self.api.post(f"/api/admin/imports/source-products/{no_price.pk}/approve/",
                            {"category": self.category.pk}, format="json")
        self.assertEqual(res.json()["error"]["code"], "price_required")
        res = self.api.post(f"/api/admin/imports/source-products/{no_price.pk}/approve/",
                            {"category": self.category.pk, "price": 990000}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        variant = Product.objects.get(pk=res.json()["product_id"]).variants.get()
        self.assertEqual(variant.availability.state, "unavailable")  # source said out of stock

    def test_linked_source_cannot_be_rejected(self, _img):
        self.import_fixture()
        sp = priced().first()
        self.api.post(f"/api/admin/imports/source-products/{sp.pk}/approve/", {"category": self.category.pk}, format="json")
        res = self.api.post(f"/api/admin/imports/source-products/{sp.pk}/reject/", {}, format="json")
        self.assertEqual(res.status_code, 409)

    def test_regular_user_and_unprivileged_staff_are_refused(self, _img):
        self.import_fixture()
        sp = priced().first()
        url = f"/api/admin/imports/source-products/{sp.pk}/approve/"
        self.assertEqual(client_for().post(url).status_code, 401)
        self.assertEqual(client_for(make_user()).post(url).status_code, 403)
        viewer = make_staff("products.view_sourceproduct", "products.review_sourceproduct", "catalog.add_product")
        res = client_for(viewer).post(url, {"category": self.category.pk}, format="json")
        self.assertEqual(res.status_code, 403)  # publishing needs publish_product
        res = client_for(viewer).post("/api/admin/imports/source-products/bulk/",
                                      {"ids": [sp.pk], "operation": "approve"}, format="json")
        self.assertEqual(res.status_code, 403)


class ImportJobStatusTests(TestCase):
    def setUp(self):
        self.api = client_for(make_staff("products.view_importjob", "products.run_import"))

    def test_errors_give_completed_with_errors_and_retry_is_allowed(self):
        with mock.patch.object(importer, "stage_product", side_effect=RuntimeError("boom")):
            job = run_job(ImportJob.objects.create(source_site="m", source_type="fixture", fixture_path=str(FIXTURE)))
        self.assertEqual(job.status, "completed_with_errors")
        self.assertGreater(job.stats["discovered"], 0)
        res = self.api.post(f"/api/admin/imports/jobs/{job.pk}/retry/")
        self.assertEqual(res.json()["status"], "pending")
        job = run_job(ImportJob.objects.get(pk=job.pk))
        self.assertEqual(job.status, "completed")

    def test_cancel_pending_and_running(self):
        pending = ImportJob.objects.create(source_site="m", source_type="fixture", fixture_path=str(FIXTURE))
        self.assertEqual(self.api.post(f"/api/admin/imports/jobs/{pending.pk}/cancel/").json()["status"], "cancelled")
        self.assertEqual(self.api.post(f"/api/admin/imports/jobs/{pending.pk}/cancel/").status_code, 409)
        running = ImportJob.objects.create(source_site="m", source_type="fixture", fixture_path=str(FIXTURE))
        ImportJob.objects.filter(pk=running.pk).update(status="running")
        self.assertTrue(self.api.post(f"/api/admin/imports/jobs/{running.pk}/cancel/").json()["cancel_requested"])
        ImportJob.objects.filter(pk=running.pk).update(status="pending")
        job = run_job(ImportJob.objects.get(pk=running.pk))
        self.assertEqual(job.status, "cancelled")
