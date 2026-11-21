from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SEOFields(models.Model):
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.CharField(max_length=500, blank=True, default="")
    meta_keywords = models.CharField(max_length=500, blank=True, default="")
    canonical_url = models.URLField(max_length=1024, blank=True, default="")

    class Meta:
        abstract = True


class PublishStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class AuditLog(models.Model):
    """Append-only record of important administrative actions (who did what, when)."""

    actor = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=64, db_index=True)
    target_repr = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.action} {self.target_type}:{self.target_id}"
