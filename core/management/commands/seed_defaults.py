from django.core.management import call_command
from django.core.management.base import BaseCommand

from cms.models import StoreSettings
from payments.models import PaymentMethod
from shipping.models import ShippingMethod


class Command(BaseCommand):
    help = "Create default payment/shipping methods and store settings if missing. Idempotent."

    def handle(self, *args, **options):
        PaymentMethod.objects.get_or_create(
            code="bank-transfer",
            defaults={"name": "کارت به کارت / واریز بانکی", "is_default": True,
                      "instructions": "مبلغ سفارش را به یکی از کارت‌های فروشگاه واریز و رسید را بارگذاری کنید."},
        )
        ShippingMethod.objects.get_or_create(code="post", defaults={"name": "پست پیشتاز", "cost": 60000})
        ShippingMethod.objects.get_or_create(code="courier", defaults={"name": "پیک (تهران)", "cost": 90000, "supports_tracking": False})
        StoreSettings.load()
        call_command("seed_storefront", stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS("Defaults ensured."))
