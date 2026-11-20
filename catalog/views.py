from django.db.models import Exists, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from catalog import serializers as s
from catalog.filters import ProductFilter
from catalog.models import (
    Attribute, AttributeOption, Brand, Category, CategoryAttribute, Product, ProductAttributeValue,
    ProductImage, ProductVariant,
)
from core.exceptions import ServiceError
from core.permissions import AdminModelPermission


def annotate_products(qs):
    active = ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
    return qs.annotate(
        min_price=Subquery(active.order_by("price").values("price")[:1]),
        max_compare_at_price=Subquery(
            active.filter(compare_at_price__isnull=False).order_by("-compare_at_price").values("compare_at_price")[:1]
        ),
        is_available=Exists(active.filter(availability__state="available")),
    )


class PublicReadOnly(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"
    lookup_value_regex = "[^/]+"


class CategoryViewSet(PublicReadOnly):
    serializer_class = s.CategorySerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        return Category.objects.filter(is_active=True)

    @extend_schema(responses=s.CategoryTreeSerializer(many=True))
    @action(detail=False)
    def tree(self, request):
        cats = list(self.get_queryset())
        children = {}
        for c in cats:
            children.setdefault(c.parent_id, []).append(c)
        roots = children.get(None, [])
        return Response(s.CategoryTreeSerializer(roots, many=True, context={"children_map": children, "request": request}).data)

    @action(detail=True)
    def attributes(self, request, slug=None):
        cat = self.get_object()
        attrs = Attribute.objects.filter(category_attributes__category=cat, is_filterable=True).prefetch_related("options")
        return Response(s.PublicAttributeSerializer(attrs, many=True).data)


class BrandViewSet(PublicReadOnly):
    serializer_class = s.BrandSerializer
    search_fields = ["name"]
    ordering_fields = ["name"]

    def get_queryset(self):
        return Brand.objects.filter(is_active=True)


ORDERINGS = {
    "newest": ["-published_at", "-id"],
    "oldest": ["published_at", "id"],
    "price_asc": [Coalesce("min_price", 0).asc(), "-id"],
    "price_desc": [Coalesce("min_price", 0).desc(), "-id"],
    "name": ["name", "id"],
    "featured": ["-is_featured", "sort_order", "-published_at"],
}


class ProductViewSet(PublicReadOnly):
    filterset_class = ProductFilter
    filter_backends = [DjangoFilterBackend]

    def get_serializer_class(self):
        return s.ProductDetailSerializer if self.action == "retrieve" else s.ProductListSerializer

    def get_queryset(self):
        qs = annotate_products(Product.objects.published()).select_related("brand")
        images = Prefetch("images", queryset=ProductImage.objects.order_by("-is_primary", "sort_order", "id"))
        if self.action == "retrieve":
            qs = qs.prefetch_related(
                images,
                Prefetch("variants", queryset=ProductVariant.objects.select_related("availability").prefetch_related("options__attribute")),
                Prefetch("categories", queryset=Category.objects.filter(is_active=True)),
                Prefetch("attribute_values", queryset=ProductAttributeValue.objects.select_related("attribute").prefetch_related("options")),
            )
        else:
            qs = qs.prefetch_related(images)
        return qs

    @extend_schema(parameters=[OpenApiParameter("sort", str, enum=list(ORDERINGS), description="Sort order")])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def filter_queryset(self, queryset):
        qs = super().filter_queryset(queryset)
        return qs.order_by(*ORDERINGS.get(self.request.query_params.get("sort"), ORDERINGS["newest"]))


# --- admin -------------------------------------------------------------------
class AdminViewSet(viewsets.ModelViewSet):
    permission_classes = [AdminModelPermission]
    parser_classes = [JSONParser, MultiPartParser, FormParser]


class AdminCategoryViewSet(AdminViewSet):
    queryset = Category.objects.all()
    serializer_class = s.AdminCategorySerializer
    filterset_fields = ["is_active", "parent"]
    search_fields = ["name", "slug"]


class AdminBrandViewSet(AdminViewSet):
    queryset = Brand.objects.all()
    serializer_class = s.AdminBrandSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class AdminAttributeViewSet(AdminViewSet):
    queryset = Attribute.objects.prefetch_related("options")
    serializer_class = s.AdminAttributeSerializer
    filterset_fields = ["type", "is_filterable", "is_variant_attribute"]
    search_fields = ["name", "code"]


class AdminAttributeOptionViewSet(AdminViewSet):
    queryset = AttributeOption.objects.select_related("attribute")
    serializer_class = s.AdminAttributeOptionSerializer
    filterset_fields = ["attribute"]


class AdminCategoryAttributeViewSet(AdminViewSet):
    queryset = CategoryAttribute.objects.all()
    serializer_class = s.AdminCategoryAttributeSerializer
    filterset_fields = ["category", "attribute"]


class AdminProductViewSet(AdminViewSet):
    serializer_class = s.AdminProductSerializer
    filterset_fields = ["status", "brand", "is_featured", "categories"]
    search_fields = ["name", "slug", "variants__sku"]
    ordering_fields = ["created_at", "updated_at", "name", "published_at"]
    action_perms = {
        "publish": ["catalog.publish_product"],
        "unpublish": ["catalog.publish_product"],
        "archive": ["catalog.publish_product"],
    }

    def get_queryset(self):
        return Product.objects.select_related("brand").prefetch_related(
            "product_categories", "attribute_values__options", "images",
            Prefetch("variants", queryset=ProductVariant.objects.select_related("availability")),
        ).distinct()

    def _set_status(self, request, status):
        product = self.get_object()
        if status == Product.Status.PUBLISHED:
            if not product.variants.filter(is_active=True).exists():
                raise ServiceError("A product needs at least one active variant to be published.", code="no_variants")
            product.published_at = product.published_at or timezone.now()
        product.status = status
        product.save(update_fields=["status", "published_at", "updated_at"])
        return Response(self.get_serializer(product).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return self._set_status(request, Product.Status.PUBLISHED)

    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        return self._set_status(request, Product.Status.DRAFT)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self._set_status(request, Product.Status.ARCHIVED)


class AdminVariantViewSet(AdminViewSet):
    queryset = ProductVariant.objects.select_related("availability", "product").prefetch_related("options")
    serializer_class = s.AdminVariantSerializer
    filterset_fields = ["product", "is_active", "availability__state"]
    search_fields = ["sku", "label", "product__name", "barcode"]

    def perform_destroy(self, instance):
        if instance.order_items.exists() or instance.reservations.exists():
            raise ServiceError("Variant has order history; deactivate it instead.", code="variant_in_use")
        instance.delete()


class AdminProductImageViewSet(AdminViewSet):
    queryset = ProductImage.objects.all()
    serializer_class = s.AdminProductImageSerializer
    filterset_fields = ["product", "variant"]
