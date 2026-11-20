"""Category-independent catalog.

Every ProductVariant is exactly ONE sellable unit; availability lives in
``inventory.VariantAvailability`` (no numeric stock anywhere).
Prices are integer Toman.
"""
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from core.files import ImageUploadValidator
from core.models import PublishStatus, SEOFields, TimeStampedModel


def unique_slug(instance, value, max_length=255):
    base = slugify(value, allow_unicode=True)[: max_length - 8] or "item"
    slug, i = base, 2
    model = type(instance)
    while model.objects.filter(slug=slug).exclude(pk=instance.pk).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


class SlugMixin(models.Model):
    slug_source = "name"

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, getattr(self, self.slug_source))
        super().save(*args, **kwargs)


class Category(SlugMixin, SEOFields, TimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    description = models.TextField(blank=True, default="")
    image = models.ImageField(upload_to="categories/", blank=True, validators=[ImageUploadValidator()])
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)
    attributes = models.ManyToManyField("Attribute", through="CategoryAttribute", related_name="categories", blank=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def descendant_ids(self):
        ids, frontier = [self.pk], [self.pk]
        while frontier:
            frontier = list(Category.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
            ids.extend(frontier)
        return ids


class Brand(SlugMixin, SEOFields, TimeStampedModel):
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True, blank=True)
    logo = models.ImageField(upload_to="brands/", blank=True, validators=[ImageUploadValidator()])
    description = models.TextField(blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Attribute(TimeStampedModel):
    class Type(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Number"
        BOOLEAN = "boolean", "Boolean"
        OPTION = "option", "Single option"
        MULTI_OPTION = "multi_option", "Multiple options"

    name = models.CharField(max_length=255)
    code = models.SlugField(max_length=100, unique=True)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.TEXT)
    unit = models.CharField(max_length=50, blank=True, default="")
    is_filterable = models.BooleanField(default=False)
    is_variant_attribute = models.BooleanField(
        default=False, help_text="Used to distinguish variants (e.g. volume, color, storage)."
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class AttributeOption(models.Model):
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="options")
    value = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, allow_unicode=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["attribute", "sort_order", "value"]
        constraints = [models.UniqueConstraint(fields=["attribute", "slug"], name="uniq_attribute_option_slug")]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.value, allow_unicode=True)[:255] or "option"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"


class CategoryAttribute(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="category_attributes")
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="category_attributes")
    is_required = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        constraints = [models.UniqueConstraint(fields=["category", "attribute"], name="uniq_category_attribute")]


class PublishedProductQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=PublishStatus.PUBLISHED, published_at__lte=timezone.now())


class Product(SlugMixin, SEOFields, TimeStampedModel):
    Status = PublishStatus

    name = models.CharField(max_length=512)
    slug = models.SlugField(max_length=255, unique=True, allow_unicode=True, blank=True)
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    categories = models.ManyToManyField(Category, through="ProductCategory", related_name="products", blank=True)
    short_description = models.CharField(max_length=1000, blank=True, default="")
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=PublishStatus.choices, default=PublishStatus.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    sort_order = models.IntegerField(default=0)

    objects = PublishedProductQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        permissions = [("publish_product", "Can publish/archive products")]
        indexes = [models.Index(fields=["status", "published_at"])]

    def __str__(self):
        return self.name

    @property
    def primary_category(self):
        link = next((pc for pc in self.product_categories.all() if pc.is_primary), None)
        return link.category if link else None


class ProductCategory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="product_categories")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="product_links")
    is_primary = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "category"], name="uniq_product_category"),
            models.UniqueConstraint(fields=["product"], condition=Q(is_primary=True), name="uniq_primary_category"),
        ]


class ProductAttributeValue(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attribute_values")
    attribute = models.ForeignKey(Attribute, on_delete=models.PROTECT, related_name="product_values")
    options = models.ManyToManyField(AttributeOption, blank=True, related_name="product_values")
    value_text = models.CharField(max_length=1000, blank=True, default="")
    value_number = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    value_boolean = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "attribute"], name="uniq_product_attribute")]


class ProductVariant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=255, blank=True, default="", help_text="e.g. '100ml – Tester'")
    options = models.ManyToManyField(AttributeOption, blank=True, related_name="variants")
    price = models.PositiveBigIntegerField(help_text="Selling price, integer Toman")
    compare_at_price = models.PositiveBigIntegerField(null=True, blank=True)
    source_price = models.PositiveBigIntegerField(
        null=True, blank=True, help_text="Last price seen at the import source. Never overwrites price."
    )
    source_compare_at_price = models.PositiveBigIntegerField(null=True, blank=True)
    source_price_updated_at = models.DateTimeField(null=True, blank=True)
    barcode = models.CharField(max_length=64, blank=True, default="")
    weight_grams = models.PositiveIntegerField(null=True, blank=True)
    condition_note = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["product", "sort_order", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(price__gt=0), name="variant_price_positive"),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True) | Q(compare_at_price__gte=models.F("price")),
                name="variant_compare_at_gte_price",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} – {self.label or self.sku}"

    @property
    def display_label(self):
        return self.label or ", ".join(o.value for o in self.options.all())


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.SET_NULL, related_name="images")
    image = models.ImageField(upload_to="products/%Y/%m/", validators=[ImageUploadValidator()])
    alt_text = models.CharField(max_length=255, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    source_url = models.URLField(max_length=1024, blank=True, default="")

    class Meta:
        ordering = ["-is_primary", "sort_order", "id"]
