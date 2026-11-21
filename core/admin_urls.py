from rest_framework.routers import DefaultRouter

from core import views

router = DefaultRouter()
router.register("audit-logs", views.AdminAuditLogViewSet, basename="admin-audit-log")
urlpatterns = router.urls
