from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from core.views import HealthCheckView
from shipping.views import ShippingMethodViewSet

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
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/health/", HealthCheckView.as_view(), name="health-root"),
    path("api/v1/", include(public_v1)),
    path("api/admin/", include(admin_api)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
