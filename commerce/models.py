from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel
from core.utils import random_code


class Cart(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveSmallIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["cart", "variant"], name="uniq_cart_variant"),
            models.CheckConstraint(condition=Q(quantity=1), name="cartitem_quantity_is_one"),
        ]


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        PAYMENT_SUBMITTED = "payment_submitted", "Payment submitted (under review)"
        PAID = "paid", "Paid"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.AWAITING_PAYMENT, db_index=True)

    # Address snapshot (immutable copy at order time)
    recipient_name = models.CharField(max_length=200)
    recipient_phone = models.CharField(max_length=11)
    province = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    address_line = models.TextField()
    postal_code = models.CharField(max_length=10)
    plaque = models.CharField(max_length=20, blank=True, default="")
    unit = models.CharField(max_length=20, blank=True, default="")

    # Shipping snapshot
    shipping_method = models.ForeignKey("shipping.ShippingMethod", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    shipping_method_name = models.CharField(max_length=255, blank=True, default="")

    subtotal = models.PositiveBigIntegerField(default=0)
    shipping_cost = models.PositiveBigIntegerField(default=0)
    discount_total = models.PositiveBigIntegerField(default=0)
    total = models.PositiveBigIntegerField(default=0)

    customer_note = models.TextField(blank=True, default="")
    admin_note = models.TextField(blank=True, default="")
    needs_review = models.BooleanField(default=False, db_index=True)
    review_reason = models.CharField(max_length=500, blank=True, default="")
    class RefundStatus(models.TextChoices):
        NONE = "none", "No refund"
        PENDING = "pending", "Refund pending (manual transfer required)"
        REFUNDED = "refunded", "Refund recorded"

    refund_status = models.CharField(max_length=20, choices=RefundStatus.choices, default=RefundStatus.NONE, db_index=True)
    refund_reference = models.CharField(max_length=100, blank=True, default="")
    refunded_at = models.DateTimeField(null=True, blank=True)
    refunded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("manage_order_status", "Can change order status"),
            ("record_refund", "Can record manual refunds"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(total=models.F("subtotal") + models.F("shipping_cost") - models.F("discount_total")),
                name="order_total_consistent",
            ),
        ]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.generate_number()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_number():
        while True:
            number = "NB" + random_code(10, "ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
            if not Order.objects.filter(number=number).exists():
                return number

    def flag_for_review(self, reason):
        self.needs_review = True
        self.review_reason = reason[:500]
        self.save(update_fields=["needs_review", "review_reason", "updated_at"])


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", null=True, on_delete=models.SET_NULL, related_name="order_items")
    product_id_snapshot = models.BigIntegerField(null=True)
    product_name = models.CharField(max_length=512)
    product_slug = models.CharField(max_length=255, blank=True, default="")
    sku = models.CharField(max_length=64)
    variant_label = models.CharField(max_length=255, blank=True, default="")
    image_url = models.CharField(max_length=1024, blank=True, default="")
    unit_price = models.PositiveBigIntegerField()
    compare_at_price = models.PositiveBigIntegerField(null=True, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1, editable=False)
    line_total = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity=1), name="orderitem_quantity_is_one"),
            models.CheckConstraint(condition=Q(line_total=models.F("unit_price")), name="orderitem_line_total_eq_price"),
            models.UniqueConstraint(fields=["order", "variant"], name="uniq_order_variant"),
        ]


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=30, blank=True, default="")
    to_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "order status history"


class OrderNote(models.Model):
    """Internal staff note on an order. Never exposed to customers."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
