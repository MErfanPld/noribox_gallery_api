from rest_framework.routers import DefaultRouter

from accounts import views

router = DefaultRouter()
router.register("users", views.AdminUserViewSet, basename="admin-user")
router.register("roles", views.AdminRoleViewSet, basename="admin-role")
urlpatterns = router.urls
