from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from cms import serializers as s
from cms import cache as cms_cache
from cms import navigation
from cms.homepage import homepage_payload
from cms.models import (
    FAQ, Banner, BlogCategory, BlogPost, HomepageSection, NavigationItem, NavigationMenu, Page, SEOMetadata, Slide,
    Slider, StoreSettings,
)
from core import audit
from core.exceptions import ServiceError
from core.permissions import AdminModelPermission


class Public(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"
    lookup_value_regex = "[^/]+"


class PageViewSet(Public):
    serializer_class = s.PageSerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        return Page.objects.published()


class BlogCategoryViewSet(Public):
    serializer_class = s.BlogCategorySerializer
    pagination_class = None
    filter_backends = []
    queryset = BlogCategory.objects.filter(is_active=True)


class BlogPostViewSet(Public):
    search_fields = ["title", "excerpt", "tags"]
    filterset_fields = {"category__slug": ["exact"]}

    def get_queryset(self):
        return BlogPost.objects.published().select_related("category", "author")

    def get_serializer_class(self):
        return s.BlogPostDetailSerializer if self.action == "retrieve" else s.BlogPostListSerializer


class FAQViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.AllowAny]
    serializer_class = s.FAQSerializer
    pagination_class = None
    filterset_fields = ["group"]
    queryset = FAQ.objects.filter(is_active=True)


class BannerViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.AllowAny]
    serializer_class = s.BannerSerializer
    pagination_class = None
    filterset_fields = ["position"]

    def get_queryset(self):
        now = timezone.now()
        return Banner.objects.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now), Q(ends_at__isnull=True) | Q(ends_at__gte=now)
        )


class SliderViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.AllowAny]
    serializer_class = s.SliderSerializer
    pagination_class = None
    filter_backends = []
    lookup_field = "code"
    queryset = Slider.objects.filter(is_active=True).prefetch_related(Prefetch("slides", queryset=Slide.objects.order_by("sort_order")))


class StoreSettingsView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["cms"], responses=s.PublicStoreSettingsSerializer)
    def get(self, request):
        return Response(cms_cache.cached(
            f"settings:{request.get_host()}",
            lambda: s.PublicStoreSettingsSerializer(StoreSettings.load(), context={"request": request}).data))


class NavigationView(APIView):
    """Published navigation for a placement (``header`` / ``footer``)."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["cms"], responses={200: dict})
    def get(self, request, location):
        data = cms_cache.cached(f"nav:{location}", lambda: navigation.public_menu(location))
        if data is None:
            raise ServiceError("Menu not found.", code="not_found", status_code=404)
        return Response(data)


class HomepageView(APIView):
    """Active homepage sections in order, with their resolved products/categories/brands/posts."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["cms"], responses={200: dict})
    def get(self, request):
        return Response(cms_cache.cached(f"home:{request.get_host()}", lambda: homepage_payload(request), timeout=60))


class SEOMetadataView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["cms"], parameters=[OpenApiParameter("path", str, required=True)], responses=s.SEOMetadataSerializer)
    def get(self, request):
        path = request.query_params.get("path")
        if not path:
            raise ServiceError("path is required", code="path_required")
        obj = SEOMetadata.objects.filter(path=path).first()
        if obj is None:
            raise ServiceError("Not found.", code="not_found", status_code=404)
        return Response(s.SEOMetadataSerializer(obj, context={"request": request}).data)


# --- admin -------------------------------------------------------------------
def admin_viewset(model_cls, serializer_cls, **attrs):
    body = {
        "queryset": model_cls.objects.all(),
        "serializer_class": serializer_cls,
        "permission_classes": [AdminModelPermission],
        "parser_classes": [JSONParser, MultiPartParser, FormParser],
        **attrs,
    }
    return type(f"Admin{model_cls.__name__}ViewSet", (viewsets.ModelViewSet,), body)


AdminPageViewSet = admin_viewset(Page, s.AdminPageSerializer, filterset_fields=["status", "show_in_header", "show_in_footer"],
                                 search_fields=["title", "slug"], ordering_fields=["sort_order", "title", "updated_at", "published_at"])
AdminBlogCategoryViewSet = admin_viewset(BlogCategory, s.AdminBlogCategorySerializer, filterset_fields=["is_active"],
                                         search_fields=["name", "slug"])
_BlogBase = admin_viewset(BlogPost, s.AdminBlogPostSerializer, filterset_fields=["status", "category", "is_featured"],
                          search_fields=["title", "slug", "tags"], ordering_fields=["published_at", "updated_at", "title"])


class AdminBlogPostViewSet(_BlogBase):
    queryset = BlogPost.objects.select_related("author", "category").prefetch_related("related_posts")

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)


AdminFAQViewSet = admin_viewset(FAQ, s.AdminFAQSerializer, filterset_fields=["group", "is_active"], search_fields=["question"])
AdminBannerViewSet = admin_viewset(Banner, s.AdminBannerSerializer, filterset_fields=["position", "is_active"],
                                   search_fields=["title"])
AdminSliderViewSet = admin_viewset(Slider, s.AdminSliderSerializer)
AdminSlideViewSet = admin_viewset(Slide, s.AdminSlideSerializer, filterset_fields=["slider"])
AdminSEOMetadataViewSet = admin_viewset(SEOMetadata, s.AdminSEOMetadataSerializer, search_fields=["path"])


class AdminStoreSettingsView(APIView):
    permission_classes = [AdminModelPermission]
    queryset = StoreSettings.objects.all()
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(tags=["admin-cms"], responses=s.AdminStoreSettingsSerializer)
    def get(self, request):
        return Response(s.AdminStoreSettingsSerializer(StoreSettings.load(), context={"request": request}).data)

    @extend_schema(tags=["admin-cms"], request=s.AdminStoreSettingsSerializer, responses=s.AdminStoreSettingsSerializer)
    def patch(self, request):
        ser = s.AdminStoreSettingsSerializer(StoreSettings.load(), data=request.data, partial=True, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save()
        audit.record(request.user, "settings.update", ser.instance,
                     {"fields": sorted(k for k in request.data.keys() if k in ser.fields)})
        return Response(ser.data)


class AdminNavigationMenuViewSet(viewsets.ModelViewSet):
    queryset = NavigationMenu.objects.select_related("published_by")
    serializer_class = s.AdminNavigationMenuSerializer
    permission_classes = [AdminModelPermission]
    pagination_class = None
    filter_backends = []
    action_perms = {"publish": ["cms.publish_navigationmenu"], "reorder": ["cms.change_navigationitem"],
                    "preview": ["cms.view_navigationmenu"], "tree": ["cms.view_navigationmenu"]}

    @extend_schema(responses={200: dict})
    @action(detail=True)
    def tree(self, request, pk=None):
        """Editor tree: every item, including disabled ones and links whose target is offline."""
        return Response(navigation.build_tree(self.get_object(), include_inactive=True))

    @extend_schema(responses={200: dict})
    @action(detail=True)
    def preview(self, request, pk=None):
        """Exactly what the storefront would get if the draft were published now."""
        menu = self.get_object()
        return Response({"location": menu.location, "name": menu.name,
                         "items": navigation.expand_auto_children(navigation.build_tree(menu))})

    @extend_schema(request=None, responses=s.AdminNavigationMenuSerializer)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return Response(self.get_serializer(navigation.publish(self.get_object(), request.user)).data)

    @extend_schema(request=s.ReorderSerializer, responses={200: dict})
    @action(detail=True, methods=["post"])
    def reorder(self, request, pk=None):
        ser = s.ReorderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        menu = self.get_object()
        navigation.reorder(menu, ser.validated_data["items"], request.user)
        return Response(navigation.build_tree(menu, include_inactive=True))


class AdminNavigationItemViewSet(viewsets.ModelViewSet):
    queryset = NavigationItem.objects.select_related("parent")
    serializer_class = s.AdminNavigationItemSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["menu", "parent", "is_active", "link_type"]
    search_fields = ["label", "url"]
    pagination_class = None


class AdminHomepageSectionViewSet(viewsets.ModelViewSet):
    queryset = HomepageSection.objects.prefetch_related("products", "categories", "brands")
    serializer_class = s.AdminHomepageSectionSerializer
    permission_classes = [AdminModelPermission]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filterset_fields = ["kind", "is_active"]
    pagination_class = None
    action_perms = {"reorder": ["cms.change_homepagesection"], "preview": ["cms.view_homepagesection"]}

    @extend_schema(request=s.HomepageReorderSerializer, responses={200: dict})
    @action(detail=False, methods=["post"])
    def reorder(self, request):
        ser = s.HomepageReorderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ids = ser.validated_data["ids"]
        sections = {x.pk: x for x in HomepageSection.objects.filter(pk__in=ids)}
        if len(sections) != len(set(ids)):
            raise ServiceError("Unknown section id.", code="invalid_item")
        for i, pk in enumerate(ids):
            sections[pk].sort_order = i
        HomepageSection.objects.bulk_update(sections.values(), ["sort_order"])
        cms_cache.bump()
        audit.record(request.user, "homepage.reorder", sections[ids[0]], {"ids": ids}, target_type="cms.homepage")
        return Response({"ids": ids})

    @extend_schema(responses={200: dict})
    @action(detail=False)
    def preview(self, request):
        """Public payload built live (uncached) — what the storefront renders after saving."""
        return Response(homepage_payload(request))
