"""Catalog business rules shared by admin views and the import review workflow."""
from django.utils import timezone

from catalog.models import Product
from core import audit
from core.exceptions import ServiceError


def set_product_status(product, status, user=None):
    """Publish / unpublish (draft) / archive a product. Publishing needs an active variant."""
    if status == Product.Status.PUBLISHED:
        if not product.variants.filter(is_active=True).exists():
            raise ServiceError("A product needs at least one active variant to be published.", code="no_variants")
        # Keep an admin-set (possibly future = scheduled) date; otherwise publish now.
        product.published_at = product.published_at or timezone.now()
    previous = product.status
    product.status = status
    product.save(update_fields=["status", "published_at", "updated_at"])
    audit.record(user, f"product.{status}", product, {"from": previous})
    return product
