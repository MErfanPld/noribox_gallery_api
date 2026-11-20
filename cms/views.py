from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from cms import serializers as s
from cms.models import FAQ, Banner, BlogCategory, BlogPost, Page, SEOMetadata, Slide, Slider, StoreSettings
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
        return Response(s.PublicStoreSettingsSerializer(StoreSettings.load(), context={"request": request}).data)


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


AdminPageViewSet = admin_viewset(Page, s.AdminPageSerializer, filterset_fields=["status"], search_fields=["title"])
AdminBlogCategoryViewSet = admin_viewset(BlogCategory, s.AdminBlogCategorySerializer)
AdminBlogPostViewSet = admin_viewset(BlogPost, s.AdminBlogPostSerializer, filterset_fields=["status", "category"], search_fields=["title"])
AdminFAQViewSet = admin_viewset(FAQ, s.AdminFAQSerializer, filterset_fields=["group", "is_active"])
AdminBannerViewSet = admin_viewset(Banner, s.AdminBannerSerializer, filterset_fields=["position", "is_active"])
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
        return Response(ser.data)
