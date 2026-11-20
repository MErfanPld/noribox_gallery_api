from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from core.testutils import client_for, make_staff
from products.models import ImportJob, SourceProduct
from products.services.importer import claim_job, process_pending_jobs, recover_stale_jobs, run_job

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "mehrdadgallery_shop_sample.html"


def job(**kw):
    return ImportJob.objects.create(source_site="m", source_type="fixture", fixture_path=str(FIXTURE), **kw)


class ImportJobExecutionTests(TestCase):
    def test_claim_is_exclusive(self):
        j = job()
        self.assertTrue(claim_job(j))
        self.assertFalse(claim_job(j))
        with mock.patch("products.services.importer.run_import_from_fixture") as runner:
            run_job(j)  # already running elsewhere → not executed again
        runner.assert_not_called()

    def test_interrupted_job_is_recovered_and_completes(self):
        j = job()
        claim_job(j)  # worker claimed it, then died
        ImportJob.objects.filter(pk=j.pk).update(heartbeat_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(recover_stale_jobs(), {"requeued": 1, "failed": 0})
        j.refresh_from_db()
        self.assertEqual((j.status, j.attempts), ("pending", 1))
        self.assertEqual(process_pending_jobs(), [(j.pk, "completed")])
        j.refresh_from_db()
        self.assertEqual(j.attempts, 2)
        self.assertGreater(SourceProduct.objects.count(), 0)
        self.assertEqual(j.errors.count(), 1)  # the interruption is recorded

    def test_stale_job_fails_after_max_attempts(self):
        j = job()
        ImportJob.objects.filter(pk=j.pk).update(status="running", attempts=2, heartbeat_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(recover_stale_jobs(), {"requeued": 0, "failed": 1})
        j.refresh_from_db()
        self.assertEqual(j.status, "failed")

    def test_fresh_running_job_untouched(self):
        j = job()
        claim_job(j)
        self.assertEqual(recover_stale_jobs(), {"requeued": 0, "failed": 0})

    def test_api_queues_and_rejects_unsafe_urls(self):
        c = client_for(make_staff("products.run_import", "products.view_importjob"))
        for url in ["file:///etc/passwd", "http://127.0.0.1/shop/", "http://169.254.169.254/"]:
            self.assertEqual(c.post("/api/admin/imports/jobs/", {"source_url": url}).status_code, 400, url)
        with mock.patch("core.http._resolve", return_value=["93.184.216.34"]):
            res = c.post("/api/admin/imports/jobs/", {"source_url": "https://shop.example/shop/"})
        self.assertEqual((res.status_code, res.data["status"]), (202, "pending"))
        ImportJob.objects.filter(pk=res.data["id"]).update(status="failed")
        self.assertEqual(c.post(f"/api/admin/imports/jobs/{res.data['id']}/retry/").data["status"], "pending")
        self.assertEqual(c.post(f"/api/admin/imports/jobs/{res.data['id']}/retry/").status_code, 409)
