from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class ShippingMethod(TimeStampedModel):
    name = models.CharField(max_length=255)
    code = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True, default="")
    cost = models.PositiveBigIntegerField(default=0, help_text="Integer Toman")
    free_shipping_threshold = models.PositiveBigIntegerField(
        null=True, blank=True, help_text="Order subtotal (Toman) at or above which shipping is free"
    )
    estimated_days_min = models.PositiveSmallIntegerField(null=True, blank=True)
    estimated_days_max = models.PositiveSmallIntegerField(null=True, blank=True)
    supports_tracking = models.BooleanField(default=True)
    tracking_url_template = models.CharField(
        max_length=500, blank=True, default="", help_text="e.g. https://tracking.post.ir/?id={code}"
    )
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name

    def cost_for(self, subtotal):
        if self.free_shipping_threshold is not None and subtotal >= self.free_shipping_threshold:
            return 0
        return self.cost


class Shipment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PREPARING = "preparing", "Preparing"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        RETURNED = "returned", "Returned"
        CANCELLED = "cancelled", "Cancelled"

    order = models.OneToOneField("commerce.Order", on_delete=models.CASCADE, related_name="shipment")
    method = models.ForeignKey(ShippingMethod, null=True, blank=True, on_delete=models.SET_NULL, related_name="shipments")
    method_name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    carrier = models.CharField(max_length=255, blank=True, default="")
    tracking_code = models.CharField(max_length=128, blank=True, default="")
    tracking_url = models.URLField(max_length=1024, blank=True, default="")
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=~Q(status="shipped") | Q(shipped_at__isnull=False), name="shipment_shipped_at_set"),
        ]
