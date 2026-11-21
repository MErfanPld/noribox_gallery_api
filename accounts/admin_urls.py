from django.urls import path
from rest_framework.routers import DefaultRouter

from accounts import views

router = DefaultRouter()
router.register("users", views.AdminUserViewSet, basename="admin-user")
router.register("roles", views.AdminRoleViewSet, basename="admin-role")
router.register("customers", views.AdminCustomerViewSet, basename="admin-customer")
urlpatterns = [path("me/", views.AdminMeView.as_view(), name="admin-me"), *router.urls]
