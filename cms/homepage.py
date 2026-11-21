"""Public homepage payload: published sections with their resolved content."""
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from catalog.models import Brand, Category, Product, ProductImage
from catalog.serializers import BrandSerializer, CategorySerializer, ProductListSerializer
from catalog.views import annotate_products
from cms.models import Banner, BlogPost, HomepageSection

K = HomepageSection.Kind


def _products(qs, limit, ctx):
    images = Prefetch("images", queryset=ProductImage.objects.order_by("-is_primary", "sort_order", "id"))
    qs = annotate_products(qs).select_related("brand").prefetch_related(images)[:limit]
    return ProductListSerializer(qs, many=True, context=ctx).data


def _section_payload(section, ctx):
    from cms.serializers import BannerSerializer, BlogPostListSerializer

    data = {
        "id": section.pk, "kind": section.kind, "eyebrow": section.eyebrow, "title": section.title,
        "subtitle": section.subtitle, "image_alt": section.image_alt,
        "image": ctx["request"].build_absolute_uri(section.image.url) if section.image else None,
        "cta_text": section.cta_text, "cta_url": section.cta_url,
        "secondary_cta_text": section.secondary_cta_text, "secondary_cta_url": section.secondary_cta_url,
        "items": section.items or [], "limit": section.limit,
    }
    published = Product.objects.published()
    if section.kind == K.NEW_ARRIVALS:
        data["products"] = _products(published.order_by("-published_at", "-id"), section.limit, ctx)
    elif section.kind == K.FEATURED_PRODUCTS:
        picked = list(section.products.values_list("pk", flat=True))
        qs = published.filter(pk__in=picked) if picked else published.filter(is_featured=True)
        rows = _products(qs.order_by("sort_order", "-published_at"), section.limit, ctx)
        if picked:  # keep the admin's chosen order
            order = {pk: i for i, pk in enumerate(picked)}
            rows = sorted(rows, key=lambda r: order.get(r["id"], 0))
        data["products"] = rows
    elif section.kind == K.CATEGORIES:
        picked = list(section.categories.filter(is_active=True))
        cats = picked or list(Category.objects.filter(is_active=True, parent=None))
        live = Q(product_links__product__status="published", product_links__product__published_at__lte=timezone.now())
        counts = dict(Category.objects.filter(pk__in=[c.pk for c in cats]).annotate(n=Count("product_links", filter=live))
                      .values_list("pk", "n"))
        data["categories"] = [dict(CategorySerializer(c, context=ctx).data, product_count=counts.get(c.pk, 0))
                              for c in cats[: max(section.limit, 1)]]
    elif section.kind == K.BRANDS:
        picked = list(section.brands.filter(is_active=True))
        data["brands"] = BrandSerializer(picked or Brand.objects.filter(is_active=True)[: section.limit],
                                         many=True, context=ctx).data
    elif section.kind == K.JOURNAL:
        posts = BlogPost.objects.published().select_related("category")[: section.limit]
        data["posts"] = BlogPostListSerializer(posts, many=True, context=ctx).data
    elif section.kind == K.PROMO_BANNERS:
        now = timezone.now()
        banners = Banner.objects.filter(is_active=True, position=section.banner_position or "home").filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now), Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        data["banners"] = BannerSerializer(banners, many=True, context=ctx).data
    return data


def homepage_payload(request):
    ctx = {"request": request}
    sections = HomepageSection.objects.filter(is_active=True).prefetch_related("categories", "brands")
    return {"sections": [_section_payload(s, ctx) for s in sections]}
