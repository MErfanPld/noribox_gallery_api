from django.urls import path
from rest_framework.routers import DefaultRouter

from accounts import views

router = DefaultRouter()
router.register("account/addresses", views.AddressViewSet, basename="address")

urlpatterns = [
    path("auth/otp/request/", views.OTPRequestView.as_view(), name="otp-request"),
    path("auth/otp/login/", views.OTPLoginView.as_view(), name="otp-login"),
    path("auth/login/", views.PasswordLoginView.as_view(), name="password-login"),
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/password/reset/", views.PasswordResetView.as_view(), name="password-reset"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("account/me/", views.MeView.as_view(), name="me"),
    path("account/password/", views.ChangePasswordView.as_view(), name="change-password"),
    *router.urls,
]
