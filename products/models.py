"""Import staging: SourceProduct records scraped from external sources, plus
import job history. Source records are NEVER published automatically; an
administrator reviews them and explicitly creates/links catalog products.

(``SourceProduct`` was previously named ``Product``; renamed in migration 0003
with data preserved.)
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class SourceProduct(models.Model):
    class Availability(models.TextChoices):
        IN_STOCK = "in_stock", "In stock"
        OUT_OF_STOCK = "out_of_stock", "Out of stock"
        UNKNOWN = "unknown", "Unknown"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        LINKED = "linked", "Linked to catalog"
        IGNORED = "ignored", "Ignored"

    source_site = models.CharField(max_length=64, db_index=True)
    source_product_id = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=512)
    product_url = models.URLField(max_length=1024)
    regular_price = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    sale_price = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    currency = models.CharField(max_length=32, default="تومان")
    availability = models.CharField(max_length=20, choices=Availability.choices, default=Availability.UNKNOWN)
    categories = models.ManyToManyField(
        "categories.Category", related_name="products", blank=True, verbose_name="source categories"
    )
    # Source-reported quantity only; null means unknown. Never used as internal stock.
    stock_quantity = models.PositiveIntegerField(null=True, blank=True, default=None)
    image_url = models.URLField(max_length=1024, null=True, blank=True)
    source_last_seen_at = models.DateTimeField(null=True, blank=True)
    last_successful_sync_at = models.DateTimeField(null=True, blank=True)
    raw_data = models.JSONField(null=True, blank=True)
    review_status = models.CharField(
        max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING, db_index=True
    )
    linked_product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="source_products"
    )
    linked_variant = models.ForeignKey(
        "catalog.ProductVariant", null=True, blank=True, on_delete=models.SET_NULL, related_name="source_products"
    )
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_site", "source_product_id"], name="uniq_source_product")
        ]
        ordering = ["-updated_at"]
        permissions = [("review_sourceproduct", "Can review source products and create catalog drafts")]

    def __str__(self) -> str:
        return f"{self.name} ({self.source_site}:{self.source_product_id})"

    @property
    def effective_price(self):
        price = self.sale_price or self.regular_price
        return int(price) if price is not None else None

    @property
    def effective_compare_at(self):
        if self.sale_price and self.regular_price and self.regular_price > self.sale_price:
            return int(self.regular_price)
        return None

    def mark_synced(self) -> None:
        now = timezone.now()
        self.source_last_seen_at = now
        self.last_successful_sync_at = now


class ImportJob(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class SourceType(models.TextChoices):
        URL = "url", "Live URL"
        FIXTURE = "fixture", "Local HTML file"
        UPLOAD = "upload", "Uploaded HTML"

    source_site = models.CharField(max_length=64)
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.URL)
    source_url = models.URLField(max_length=1024, blank=True, default="")
    fixture_path = models.CharField(max_length=1024, blank=True, default="")
    limit = models.PositiveIntegerField(null=True, blank=True)
    dry_run = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    stats = models.JSONField(default=dict, blank=True)
    log = models.TextField(blank=True, default="")
    source_html = models.TextField(blank=True, default="", help_text="Uploaded HTML awaiting processing")
    attempts = models.PositiveSmallIntegerField(default=0)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        permissions = [("run_import", "Can run product imports")]


class ImportItem(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        UNCHANGED = "unchanged", "Unchanged"
        WOULD_CREATE = "would_create", "Would create (dry-run)"
        WOULD_UPDATE = "would_update", "Would update (dry-run)"
        DUPLICATE = "duplicate", "Duplicate"
        SKIPPED = "skipped", "Skipped"
        ERROR = "error", "Error"

    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="items")
    source_product = models.ForeignKey(SourceProduct, null=True, blank=True, on_delete=models.SET_NULL, related_name="import_items")
    external_id = models.CharField(max_length=64, blank=True, default="", help_text="Source-site product id")
    name = models.CharField(max_length=512, blank=True, default="")
    action = models.CharField(max_length=20, choices=Action.choices)
    changes = models.JSONField(default=dict, blank=True)
    parsed_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class ImportErrorLog(models.Model):
    """An error recorded during an import job (the spec's "ImportError")."""

    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="errors")
    item = models.ForeignKey(ImportItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="errors")
    external_id = models.CharField(max_length=64, blank=True, default="")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "import error"
