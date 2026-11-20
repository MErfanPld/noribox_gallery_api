"""Exclusive, state-based inventory: one sellable unit per ProductVariant."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class AvailabilityState(models.TextChoices):
    AVAILABLE = "available", "Available"
    RESERVED = "reserved", "Reserved"
    UNAVAILABLE = "unavailable", "Unavailable"


class VariantAvailability(models.Model):
    variant = models.OneToOneField("catalog.ProductVariant", on_delete=models.CASCADE, related_name="availability")
    state = models.CharField(
        max_length=20, choices=AvailabilityState.choices, default=AvailabilityState.AVAILABLE, db_index=True
    )
    sold = models.BooleanField(default=False, help_text="True once the unit was sold through an approved payment.")
    note = models.CharField(max_length=500, blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "variant availabilities"
        permissions = [("manage_inventory", "Can change availability / release reservations")]

    def __str__(self):
        return f"{self.variant_id}: {self.state}"


class Reservation(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CONVERTED = "converted", "Converted to sale"
        RELEASED = "released", "Released"
        EXPIRED = "expired", "Expired"

    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="reservations")
    order = models.ForeignKey("commerce.Order", on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A variant can have at most ONE active reservation (DB-enforced).
            models.UniqueConstraint(fields=["variant"], condition=Q(status="active"), name="uniq_active_reservation_per_variant"),
        ]
