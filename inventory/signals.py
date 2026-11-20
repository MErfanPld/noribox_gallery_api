from django.db.models.signals import post_save
from django.dispatch import receiver

from catalog.models import ProductVariant
from inventory.models import VariantAvailability


@receiver(post_save, sender=ProductVariant)
def create_availability(sender, instance, created, **kwargs):
    if created:
        VariantAvailability.objects.get_or_create(variant=instance)
