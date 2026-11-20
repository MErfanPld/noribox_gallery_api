from django.db import models
from django.utils import timezone

from catalog.models import SlugMixin
from core.files import ImageUploadValidator
from core.models import PublishStatus, SEOFields, TimeStampedModel


class PublishableQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=PublishStatus.PUBLISHED).filter(
            models.Q(published_at__isnull=True) | models.Q(published_at__lte=timezone.now())
        )


class Publishable(models.Model):
    status = models.CharField(max_length=20, choices=PublishStatus.choices, default=PublishStatus.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    objects = PublishableQuerySet.as_manager()

    class Meta:
        abstract = True


class Page(SlugMixin, Publishable, SEOFields, TimeStampedModel):
    slug_source = "title"
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True, blank=True)
    body = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class BlogCategory(SlugMixin, SEOFields, TimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True, blank=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "blog categories"

    def __str__(self):
        return self.name


class BlogPost(SlugMixin, Publishable, SEOFields, TimeStampedModel):
    slug_source = "title"
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True, blank=True)
    category = models.ForeignKey(BlogCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="posts")
    author = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    excerpt = models.TextField(blank=True, default="")
    body = models.TextField(blank=True, default="")
    cover_image = models.ImageField(upload_to="blog/", blank=True, validators=[ImageUploadValidator()])
    tags = models.CharField(max_length=500, blank=True, default="", help_text="Comma-separated")

    class Meta:
        ordering = ["-published_at", "-created_at"]

    def __str__(self):
        return self.title


class FAQ(TimeStampedModel):
    question = models.CharField(max_length=500)
    answer = models.TextField()
    group = models.CharField(max_length=100, blank=True, default="", db_index=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["group", "sort_order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"


class Banner(TimeStampedModel):
    title = models.CharField(max_length=255)
    position = models.SlugField(max_length=64, db_index=True, help_text="Placement key, e.g. home_top")
    image = models.ImageField(upload_to="banners/", validators=[ImageUploadValidator()])
    mobile_image = models.ImageField(upload_to="banners/", blank=True, validators=[ImageUploadValidator()])
    link_url = models.CharField(max_length=1024, blank=True, default="")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "sort_order", "id"]


class Slider(TimeStampedModel):
    name = models.CharField(max_length=255)
    code = models.SlugField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Slide(TimeStampedModel):
    slider = models.ForeignKey(Slider, on_delete=models.CASCADE, related_name="slides")
    title = models.CharField(max_length=255, blank=True, default="")
    subtitle = models.CharField(max_length=500, blank=True, default="")
    image = models.ImageField(upload_to="slides/", validators=[ImageUploadValidator()])
    mobile_image = models.ImageField(upload_to="slides/", blank=True, validators=[ImageUploadValidator()])
    link_url = models.CharField(max_length=1024, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]


class SEOMetadata(TimeStampedModel):
    """SEO overrides for arbitrary storefront routes (e.g. '/', '/brands')."""

    path = models.CharField(max_length=500, unique=True)
    title = models.CharField(max_length=255, blank=True, default="")
    description = models.CharField(max_length=500, blank=True, default="")
    keywords = models.CharField(max_length=500, blank=True, default="")
    og_image = models.ImageField(upload_to="seo/", blank=True, validators=[ImageUploadValidator()])
    canonical_url = models.URLField(max_length=1024, blank=True, default="")
    noindex = models.BooleanField(default=False)

    class Meta:
        ordering = ["path"]
        verbose_name_plural = "SEO metadata"


class StoreSettings(models.Model):
    """Singleton store-wide settings (pk=1)."""

    store_name = models.CharField(max_length=255, default="Noribox Gallery")
    tagline = models.CharField(max_length=255, blank=True, default="")
    logo = models.ImageField(upload_to="store/", blank=True, validators=[ImageUploadValidator()])
    favicon = models.ImageField(upload_to="store/", blank=True, validators=[ImageUploadValidator()])
    support_phone = models.CharField(max_length=20, blank=True, default="")
    whatsapp_number = models.CharField(max_length=20, blank=True, default="", help_text="International format for wa.me, e.g. 98912...")
    email = models.EmailField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    instagram = models.CharField(max_length=255, blank=True, default="")
    telegram = models.CharField(max_length=255, blank=True, default="")
    working_hours = models.CharField(max_length=255, blank=True, default="")
    admin_notification_phones = models.CharField(max_length=500, blank=True, default="", help_text="Comma-separated, private")
    payment_instructions = models.TextField(blank=True, default="")
    default_meta_title = models.CharField(max_length=255, blank=True, default="")
    default_meta_description = models.CharField(max_length=500, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "store settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # singleton
        return (0, {})

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def admin_phone_list(self):
        return [p.strip() for p in self.admin_notification_phones.split(",") if p.strip()]
