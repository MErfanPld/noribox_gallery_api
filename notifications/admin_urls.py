from rest_framework.routers import DefaultRouter

from notifications import views

router = DefaultRouter()
router.register("notifications/sms-templates", views.AdminSMSTemplateViewSet, basename="admin-sms-template")
router.register("notifications/sms-logs", views.AdminSMSLogViewSet, basename="admin-sms-log")
urlpatterns = router.urls
