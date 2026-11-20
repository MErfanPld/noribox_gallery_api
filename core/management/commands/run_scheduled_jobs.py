from django.core.management.base import BaseCommand

from commerce.services import cancel_stale_unpaid_orders
from core.locks import exclusive
from inventory.services import expire_reservations
from notifications.services import process_retries
from payments.services import retry_unreserved_receipts
from products.services.importer import recover_stale_jobs

TASKS = ("reservations", "receipts", "orders", "sms", "imports")


class Command(BaseCommand):
    help = (
        "Periodic maintenance (schedule every minute): recover unreserved receipts, expire reservations, "
        "cancel stale unpaid orders, send queued/failed SMS, recover stale import jobs. "
        "Overlapping runs are skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=TASKS, default=None)

    def handle(self, *args, **options):
        with exclusive("run_scheduled_jobs") as acquired:
            if not acquired:
                self.stdout.write("Another run_scheduled_jobs is in progress; skipping.")
                return
            only = options["only"]
            if only in (None, "receipts"):
                self.stdout.write(f"Retried reservation for {retry_unreserved_receipts()} receipt(s)")
            if only in (None, "reservations"):
                self.stdout.write(f"Expired reservations for {len(expire_reservations())} order(s)")
            if only in (None, "orders"):
                self.stdout.write(f"Cancelled {len(cancel_stale_unpaid_orders())} unpaid order(s)")
            if only in (None, "sms"):
                self.stdout.write(f"Processed {process_retries()} SMS")
            if only in (None, "imports"):
                self.stdout.write(f"Import job recovery: {recover_stale_jobs()}")
