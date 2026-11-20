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
