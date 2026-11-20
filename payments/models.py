import hashlib
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.files import private_storage
from core.models import TimeStampedModel


class PaymentMethod(TimeStampedModel):
    class Kind(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank transfer (card-to-card / SHEBA)"

    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=30, choices=Kind.choices, default=Kind.BANK_TRANSFER)
    instructions = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class StoreBankCard(TimeStampedModel):
    bank_name = models.CharField(max_length=100)
    holder_name = models.CharField(max_length=200)
    card_number = models.CharField(max_length=16)
    sheba = models.CharField(max_length=26, blank=True, default="", help_text="IRxxxxxxxxxxxxxxxxxxxxxxxx")
    account_number = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.bank_name} ****{self.card_number[-4:]}"


class Payment(TimeStampedModel):
    class Status(models.TextChoices):
        AWAITING_RECEIPT = "awaiting_receipt", "Awaiting receipt"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey("commerce.Order", on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(PaymentMethod, null=True, on_delete=models.SET_NULL, related_name="payments")
    amount = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING_RECEIPT, db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("review_payment", "Can approve or reject payment receipts")]
        constraints = [
            models.UniqueConstraint(
                fields=["order"], condition=Q(status="approved"), name="uniq_approved_payment_per_order"
            ),
        ]


def receipt_upload_to(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"receipts/{instance.payment.order_id}/{uuid.uuid4().hex}.{ext}"


class PaymentReceipt(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled with the order"

    class ReservationStatus(models.TextChoices):
        RESERVED = "reserved", "All items reserved"
        CONFLICT = "conflict", "Reservation conflict"
        NOT_ATTEMPTED = "not_attempted", "Not attempted"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="receipts")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    file = models.FileField(storage=private_storage, upload_to=receipt_upload_to, max_length=255)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    reference_number = models.CharField(max_length=100, blank=True, default="")
    payer_card_last4 = models.CharField(max_length=4, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    declared_amount = models.PositiveBigIntegerField(null=True, blank=True)
    customer_note = models.CharField(max_length=1000, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    reservation_status = models.CharField(
        max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.NOT_ATTEMPTED
    )
    reservation_detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["payment"], condition=Q(status="pending"), name="uniq_pending_receipt_per_payment"
            ),
        ]

    @staticmethod
    def digest(fileobj):
        h = hashlib.sha256()
        fileobj.seek(0)
        for chunk in iter(lambda: fileobj.read(65536), b""):
            h.update(chunk)
        fileobj.seek(0)
        return h.hexdigest()


class PaymentReview(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    receipt = models.ForeignKey(PaymentReceipt, on_delete=models.PROTECT, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decision = models.CharField(max_length=20, choices=Decision.choices)
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
