"""Admin review of staged source products → catalog drafts (never auto-published)."""
import logging
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from catalog.models import Product, ProductCategory, ProductImage, ProductVariant
from core.exceptions import ConflictError, ServiceError
from core.files import detect_kind
from core.utils import random_code
from products.models import SourceProduct
from products.services.importer import sync_linked_variant_source_price

logger = logging.getLogger("noribox")


def _generate_sku(source):
    base = f"{source.source_site[:4].upper()}-{source.source_product_id}"[:40]
    if base.startswith(f"{source.source_site[:4].upper()}-url:"):
        base = f"{source.source_site[:4].upper()}-{random_code(8)}"
    sku = base
    while ProductVariant.objects.filter(sku=sku).exists():
        sku = f"{base}-{random_code(4)}"
    return sku


def download_image(url, max_size=None):
    """Download a remote image for local storage. Returns ContentFile or None."""
    max_size = max_size or settings.PRODUCT_IMAGE_MAX_SIZE_BYTES
    try:
        req = Request(url, headers={"User-Agent": settings.IMPORT_USER_AGENT})
        with urlopen(req, timeout=settings.IMPORT_REQUEST_TIMEOUT) as resp:
            data = resp.read(max_size + 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Image download failed %s: %s", url, exc)
        return None
    kind = detect_kind(data[:16])
    if len(data) > max_size or kind not in {"jpg", "jpeg", "png", "webp", "gif"}:
        return None
    return ContentFile(data, name=f"import.{kind}")


@transaction.atomic
def create_draft_product(source, user, *, price=None, sku=None, category=None, brand=None,
                         name=None, label="", download_images=True):
    source = SourceProduct.objects.select_for_update().get(pk=source.pk)
    if source.linked_variant_id:
        raise ConflictError("Source product is already linked to a catalog variant.", code="already_linked")
    initial_price = price or source.effective_price
    if not initial_price:
        raise ServiceError("Price is required (source has no price).", code="price_required")
    product = Product.objects.create(
        name=name or source.name, brand=brand, status=Product.Status.DRAFT,
    )
    if category is not None:
        ProductCategory.objects.create(product=product, category=category, is_primary=True)
    compare = source.effective_compare_at
    variant = ProductVariant.objects.create(
        product=product,
        sku=sku or _generate_sku(source),
        label=label,
        price=initial_price,
        compare_at_price=compare if compare and compare >= initial_price else None,
        source_price=source.effective_price,
        source_compare_at_price=source.effective_compare_at,
        source_price_updated_at=timezone.now() if source.effective_price else None,
    )
    if download_images and source.image_url:
        content = download_image(source.image_url)
        if content is not None:
            img = ProductImage(product=product, variant=variant, is_primary=True, alt_text=product.name, source_url=source.image_url)
            img.image.save(content.name, content, save=True)
    _mark_linked(source, product, variant, user)
    return product


def _mark_linked(source, product, variant, user):
    source.linked_product, source.linked_variant = product, variant
    source.review_status = SourceProduct.ReviewStatus.LINKED
    source.reviewed_by, source.reviewed_at = user, timezone.now()
    source.save()


@transaction.atomic
def link_to_variant(source, variant, user):
    source = SourceProduct.objects.select_for_update().get(pk=source.pk)
    _mark_linked(source, variant.product, variant, user)
    sync_linked_variant_source_price(source)  # source_price only; price untouched
    return source


def ignore_source(source, user):
    source.review_status = SourceProduct.ReviewStatus.IGNORED
    source.reviewed_by, source.reviewed_at = user, timezone.now()
    source.save(update_fields=["review_status", "reviewed_by", "reviewed_at", "updated_at"])
    return source
