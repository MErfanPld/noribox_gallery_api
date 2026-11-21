from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from rest_framework.authentication import SessionAuthentication

from accounts.authentication import JWTAuthentication
from core.permissions import IsStaff
from core.views import HealthCheckView
from shipping.views import ShippingMethodViewSet

STAFF_ONLY = {
    "permission_classes": [IsStaff],
    "authentication_classes": [JWTAuthentication, SessionAuthentication],
}

public_v1 = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("", include("accounts.urls")),
    path("", include("catalog.urls")),
    path("", include("payments.urls")),
    path("", include("commerce.urls")),
    path("shipping-methods/", ShippingMethodViewSet.as_view({"get": "list"}), name="shipping-methods"),
    path("", include("cms.urls")),
]

admin_api = [
    path("", include("accounts.admin_urls")),
    path("", include("catalog.admin_urls")),
    path("", include("inventory.admin_urls")),
    path("", include("products.admin_urls")),
    path("", include("payments.admin_urls")),
    path("", include("commerce.admin_urls")),
    path("", include("notifications.admin_urls")),
    path("", include("cms.admin_urls")),
    path("", include("core.admin_urls")),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/health/", HealthCheckView.as_view(), name="health-root"),
    path("api/v1/", include(public_v1)),
    path("api/admin/", include(admin_api)),
    # Storefront schema is public; the management schema requires a staff user
    # (JWT, or a Django-admin session for the browsable docs).
    path("api/schema/", SpectacularAPIView.as_view(custom_settings={"PREPROCESSING_HOOKS": ["core.schema.public_endpoints"]}), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/admin/schema/", SpectacularAPIView.as_view(
        custom_settings={"PREPROCESSING_HOOKS": ["core.schema.admin_endpoints"]}, **STAFF_ONLY), name="admin-schema"),
    path("api/admin/docs/", SpectacularSwaggerView.as_view(url_name="admin-schema", **STAFF_ONLY), name="admin-swagger-ui"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
