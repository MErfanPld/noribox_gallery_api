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
    excerpt = models.CharField(max_length=500, blank=True, default="")
    body = models.TextField(blank=True, default="")
    featured_image = models.ImageField(upload_to="pages/", blank=True, validators=[ImageUploadValidator()])
    # Convenience flags: saving the page adds/removes a link to it in the header/footer
    # menu draft (see cms.navigation.sync_page_links); the admin still publishes the menu.
    show_in_header = models.BooleanField(default=False)
    show_in_footer = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "title"]

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
    is_featured = models.BooleanField(default=False)
    related_posts = models.ManyToManyField("self", blank=True, symmetrical=False, related_name="+")

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
    default_og_image = models.ImageField(upload_to="store/", blank=True, validators=[ImageUploadValidator()])
    # Footer
    footer_logo = models.ImageField(upload_to="store/", blank=True, validators=[ImageUploadValidator()])
    footer_description = models.TextField(blank=True, default="")
    copyright_text = models.CharField(max_length=255, blank=True, default="")
    newsletter_enabled = models.BooleanField(default=False)
    social_links = models.JSONField(default=list, blank=True, help_text='[{"platform": "instagram", "url": "https://..."}]')
    # Site-wide notices
    announcement_enabled = models.BooleanField(default=False)
    announcement_text = models.CharField(max_length=255, blank=True, default="")
    announcement_link = models.CharField(max_length=1024, blank=True, default="")
    maintenance_enabled = models.BooleanField(
        default=False, help_text="Shows the message site-wide and pauses new orders (checkout is refused).")
    maintenance_message = models.CharField(max_length=500, blank=True, default="")
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


# --- Navigation ---------------------------------------------------------------
class NavigationMenu(models.Model):
    """A menu placement (header, footer, …). Items are edited as a draft; ``publish``
    freezes them into ``published_data`` which is what the storefront serves."""

    class Location(models.TextChoices):
        HEADER = "header", "Header"
        FOOTER = "footer", "Footer"

    location = models.CharField(max_length=32, choices=Location.choices, unique=True)
    name = models.CharField(max_length=100)
    published_data = models.JSONField(default=list, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    draft_updated_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    MAX_DEPTH = 3  # root → dropdown → child

    class Meta:
        ordering = ["location"]
        permissions = [("publish_navigationmenu", "Can publish navigation menus")]

    def __str__(self):
        return self.name

    @property
    def has_unpublished_changes(self):
        return self.published_at is None or self.draft_updated_at > self.published_at

    def touch(self):
        NavigationMenu.objects.filter(pk=self.pk).update(draft_updated_at=timezone.now())


class NavigationItem(models.Model):
    class LinkType(models.TextChoices):
        NONE = "none", "Heading / dropdown only"
        INTERNAL = "internal", "Internal path"
        EXTERNAL = "external", "External URL"
        PAGE = "page", "CMS page"
        CATEGORY = "category", "Category"
        BRAND = "brand", "Brand"
        BLOG_CATEGORY = "blog_category", "Blog category"

    class AutoChildren(models.TextChoices):
        NONE = "", "None"
        CATEGORIES = "categories", "Categories marked for navigation"

    menu = models.ForeignKey(NavigationMenu, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    label = models.CharField(max_length=100)
    link_type = models.CharField(max_length=20, choices=LinkType.choices, default=LinkType.INTERNAL)
    url = models.CharField(max_length=1024, blank=True, default="", help_text="'/shop?…' for internal, https://… for external")
    page = models.ForeignKey(Page, null=True, blank=True, on_delete=models.CASCADE, related_name="nav_items")
    category = models.ForeignKey("catalog.Category", null=True, blank=True, on_delete=models.CASCADE, related_name="nav_items")
    brand = models.ForeignKey("catalog.Brand", null=True, blank=True, on_delete=models.CASCADE, related_name="nav_items")
    blog_category = models.ForeignKey(BlogCategory, null=True, blank=True, on_delete=models.CASCADE, related_name="nav_items")
    auto_children = models.CharField(max_length=20, choices=AutoChildren.choices, blank=True, default="")
    is_active = models.BooleanField(default=True)
    show_on_desktop = models.BooleanField(default=True)
    show_on_mobile = models.BooleanField(default=True)
    open_in_new_tab = models.BooleanField(default=False)
    nofollow = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    managed_by_page = models.BooleanField(default=False, editable=False,
                                          help_text="Created from a page's show_in_header/footer flag")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.label

    @property
    def depth(self):
        d, node = 1, self
        while node.parent_id:
            d += 1
            node = node.parent
        return d


# --- Homepage -----------------------------------------------------------------
class HomepageSection(TimeStampedModel):
    """One ordered block of the storefront homepage (not a generic page builder:
    each kind has a fixed design in the storefront; admins fill and order them)."""

    class Kind(models.TextChoices):
        HERO = "hero", "Hero"
        CATEGORIES = "categories", "Featured categories"
        NEW_ARRIVALS = "new_arrivals", "New arrivals"
        FEATURED_PRODUCTS = "featured_products", "Featured products"
        COLLECTIONS = "collections", "Attribute collections"
        BANNER = "banner", "Editorial banner"
        PROMO_BANNERS = "promo_banners", "Promotional banners"
        BRANDS = "brands", "Featured brands"
        JOURNAL = "journal", "Latest articles"
        PROMISES = "promises", "Service promises"
        TRACK_ORDER = "track_order", "Order tracking call-out"

    kind = models.CharField(max_length=32, choices=Kind.choices)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    eyebrow = models.CharField(max_length=120, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    subtitle = models.TextField(blank=True, default="")
    image = models.ImageField(upload_to="homepage/", blank=True, validators=[ImageUploadValidator()])
    image_alt = models.CharField(max_length=255, blank=True, default="")
    cta_text = models.CharField(max_length=80, blank=True, default="")
    cta_url = models.CharField(max_length=1024, blank=True, default="")
    secondary_cta_text = models.CharField(max_length=80, blank=True, default="")
    secondary_cta_url = models.CharField(max_length=1024, blank=True, default="")
    items = models.JSONField(default=list, blank=True,
                             help_text="Kind-specific list: hero stats / promises [{title, text}]")
    products = models.ManyToManyField("catalog.Product", blank=True, related_name="+")
    categories = models.ManyToManyField("catalog.Category", blank=True, related_name="+")
    brands = models.ManyToManyField("catalog.Brand", blank=True, related_name="+")
    banner_position = models.SlugField(max_length=64, blank=True, default="home",
                                       help_text="Banner placement key for promo_banners")
    limit = models.PositiveSmallIntegerField(default=8)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} #{self.pk}"
