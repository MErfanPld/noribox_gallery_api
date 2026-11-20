from django.core.management.base import BaseCommand

from commerce.services import cancel_stale_unpaid_orders
from inventory.services import expire_reservations
from notifications.services import process_retries


class Command(BaseCommand):
    help = "Run periodic maintenance: expire reservations, cancel stale unpaid orders, retry failed SMS. Schedule every 5 minutes."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=["reservations", "orders", "sms"], default=None)

    def handle(self, *args, **options):
        only = options["only"]
        if only in (None, "reservations"):
            self.stdout.write(f"Expired reservations for {len(expire_reservations())} order(s)")
        if only in (None, "orders"):
            self.stdout.write(f"Cancelled {len(cancel_stale_unpaid_orders())} unpaid order(s)")
        if only in (None, "sms"):
            self.stdout.write(f"Retried {process_retries()} SMS")
