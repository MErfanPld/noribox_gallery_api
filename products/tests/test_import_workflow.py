from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from catalog.models import Product, ProductVariant
from core.testutils import client_for, make_product, make_staff
from products.models import ImportItem, ImportJob, SourceProduct
from products.services.importer import import_products, run_job, upsert_product
from products.services.parser import ParsedProduct
from products.services.review import create_draft_product, link_to_variant

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "mehrdadgallery_shop_sample.html"


def parsed(pid="1", price="1000000", url=None, name="P"):
    return ParsedProduct(source_product_id=pid, name=name, product_url=url or f"https://x.test/p/{pid}",
                         regular_price=Decimal(price), availability="in_stock")


class ImportJobTests(TestCase):
    def test_dry_run_writes_nothing_but_records_items(self):
        job = run_job(ImportJob.objects.create(source_site="mehrdadgallery", source_type="fixture", fixture_path=str(FIXTURE), dry_run=True))
        self.assertEqual(job.status, "completed")
        self.assertFalse(SourceProduct.objects.exists())
        self.assertTrue(job.items.filter(action=ImportItem.Action.WOULD_CREATE).exists())
        self.assertGreaterEqual(job.stats["created"], 5)

    def test_job_then_reimport_updates(self):
        run_job(ImportJob.objects.create(source_site="m", source_type="fixture", fixture_path=str(FIXTURE)))
        count = SourceProduct.objects.count()
        job2 = run_job(ImportJob.objects.create(source_site="m", source_type="fixture", fixture_path=str(FIXTURE)))
        self.assertEqual(SourceProduct.objects.count(), count)
        self.assertEqual(job2.stats["created"], 0)
        self.assertEqual(job2.items.filter(action="unchanged").count(), count)
        self.assertFalse(Product.objects.exists())  # never auto-creates catalog products

    def test_failed_source_is_recorded(self):
        job = run_job(ImportJob.objects.create(source_site="m", source_type="fixture", fixture_path="/nonexistent.html"))
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.errors.count(), 1)

    def test_duplicate_by_url_detected(self):
        job = ImportJob.objects.create(source_site="m")
        stats = import_products([parsed("1", url="https://x.test/a"), parsed("2", url="https://x.test/a"), parsed("1")], "m", job=job)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["duplicates"], 2)
        self.assertEqual(SourceProduct.objects.count(), 1)

    def test_source_price_never_overwrites_selling_price(self):
        sp, _ = upsert_product(parsed("9", "1000000"), "m")
        variant = make_product(price=1_500_000).variants.first()
        link_to_variant(sp, variant, None)
        variant.refresh_from_db()
        self.assertEqual((variant.price, variant.source_price), (1_500_000, 1_000_000))
        job = ImportJob.objects.create(source_site="m")
        import_products([parsed("9", "1200000")], "m", job=job)
        variant.refresh_from_db()
        self.assertEqual((variant.price, variant.source_price), (1_500_000, 1_200_000))
        self.assertIn("regular_price", job.items.get().changes)

    def test_create_draft_is_never_published(self):
        sp, _ = upsert_product(parsed("7", "990000", name="Imported"), "m")
        product = create_draft_product(sp, None, download_images=False)
        self.assertEqual(product.status, Product.Status.DRAFT)
        v = product.variants.get()
        self.assertEqual((v.price, v.source_price), (990000, 990000))
        sp.refresh_from_db()
        self.assertEqual(sp.review_status, "linked")
        self.assertEqual(client_for().get(f"/api/v1/products/{product.slug}/").status_code, 404)

    def test_admin_api_import_upload_and_review(self):
        staff = make_staff("products.run_import", "products.add_importjob", "products.view_importjob",
                           "products.review_sourceproduct", "products.view_sourceproduct", "catalog.add_product")
        c = client_for(staff)
        with open(FIXTURE, "rb") as fh:
            res = c.post("/api/admin/imports/jobs/", {"html_file": fh, "dry_run": "false"}, format="multipart")
        self.assertEqual(res.status_code, 202, res.data)
        self.assertEqual(res.data["status"], "pending")  # queued, not run in the request
        from products.services.importer import process_pending_jobs

        self.assertEqual(process_pending_jobs(), [(res.data["id"], "completed")])
        sp = SourceProduct.objects.get(source_product_id="52474")
        res = c.post(f"/api/admin/imports/source-products/{sp.pk}/create-draft/", {"price": 4000000, "download_images": False})
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(ProductVariant.objects.get(product_id=res.data["product_id"]).price, 4_000_000)
        self.assertEqual(c.post(f"/api/admin/imports/source-products/{sp.pk}/create-draft/", {}).status_code, 409)
        self.assertEqual(client_for(make_staff("products.view_sourceproduct")).post(
            f"/api/admin/imports/source-products/{sp.pk}/ignore/").status_code, 403)
