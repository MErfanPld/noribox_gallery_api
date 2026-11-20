"""
Management command: stage products from the configured source website or a
local HTML fixture. Creates an ImportJob with per-item history. Products are
staged as SourceProduct records only; nothing is published.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from products.models import ImportJob
from products.services.importer import run_job


class Command(BaseCommand):
    help = "Stage source products (with ImportJob history). Never publishes catalog products."

    def add_arguments(self, parser):
        parser.add_argument("--url", default=None, help="Source listing URL (default: SOURCE_BASE_URL/shop/)")
        parser.add_argument("--fixture", default=None, help="Path to a local HTML fixture file")
        parser.add_argument("--limit", type=int, default=None, help="Max products to import")
        parser.add_argument("--source-site", default=None, help="Source site identifier (default: SOURCE_SITE_ID)")
        parser.add_argument("--dry-run", action="store_true", help="Preview changes; do not write SourceProducts")

    def handle(self, *args, **options):
        limit = options["limit"] if options["limit"] is not None else getattr(settings, "IMPORT_DEFAULT_LIMIT", None)
        source_site = options["source_site"] or settings.SOURCE_SITE_ID
        job = ImportJob(source_site=source_site, limit=limit, dry_run=options["dry_run"])
        if options["fixture"]:
            path = Path(options["fixture"])
            if not path.is_file():
                raise CommandError(f"Fixture file not found: {path}")
            self.stdout.write(self.style.WARNING(f"Using local fixture: {path}"))
            job.source_type, job.fixture_path = ImportJob.SourceType.FIXTURE, str(path)
        else:
            url = options["url"] or f"{settings.SOURCE_BASE_URL.rstrip('/')}/shop/"
            self.stdout.write(f"Fetching: {url}")
            job.source_type, job.source_url = ImportJob.SourceType.URL, url
        job.save()
        job = run_job(job)
        if job.status == ImportJob.Status.FAILED:
            raise CommandError(
                f"Import job #{job.pk} failed: {job.log}\nIf the source is unreachable, re-run with "
                "--fixture path/to/listing.html to test the parser against a saved HTML sample."
            )
        if job.dry_run:
            for item in job.items.all():
                self.stdout.write(f"  - [{item.action}] id={item.external_id} name={item.name[:50]!r} {item.changes or ''}")
        s = job.stats
        self.stdout.write(self.style.SUCCESS(
            f"Import job #{job.pk} {'(dry-run) ' if job.dry_run else ''}finished: created={s.get('created', 0)} "
            f"updated={s.get('updated', 0)} unchanged={s.get('unchanged', 0)} skipped={s.get('skipped', 0)} "
            f"duplicates={s.get('duplicates', 0)} errors={s.get('errors', 0)}"
        ))
