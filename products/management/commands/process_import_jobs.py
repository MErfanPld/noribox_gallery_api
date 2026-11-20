from django.core.management.base import BaseCommand

from core.locks import exclusive
from products.services.importer import process_pending_jobs


class Command(BaseCommand):
    help = "Run queued ImportJobs (created via the admin API). Schedule every minute; overlapping runs are skipped."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=5)

    def handle(self, *args, **options):
        with exclusive("process_import_jobs") as acquired:
            if not acquired:
                self.stdout.write("Another import worker is running; skipping.")
                return
            for pk, status in process_pending_jobs(options["limit"]):
                self.stdout.write(f"Import job #{pk}: {status}")
