from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from accounts import serializers as s
from accounts.models import Address, OTPCode, User
from accounts.services import (
    get_or_create_otp_user, mark_phone_verified, request_otp, revoke_all_tokens, set_password_and_revoke_tokens,
    verify_otp,
)
from core import audit, ratelimit
from core.exceptions import ConflictError, ServiceError
from core.net import get_client_ip
from core.permissions import AdminModelPermission, IsStaff


def _token_response(user, http_status=status.HTTP_200_OK):
    data = s.tokens_for(user)
    data["user"] = s.UserSerializer(user).data
    return Response(data, status=http_status)


class AuthView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "auth"

    def get_throttles(self):
        from core.throttling import ScopedRateThrottle

        return [ScopedRateThrottle(), *super().get_throttles()]


class OTPRequestView(AuthView):
    throttle_scope = "otp_request"

    @extend_schema(tags=["auth"], request=s.OTPRequestSerializer, responses={202: dict})
    def post(self, request):
        ser = s.OTPRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        phone, purpose = ser.validated_data["phone"], ser.validated_data["purpose"]
        exists = User.objects.filter(phone=phone).exists()
        # Identical response whether or not the account exists (no enumeration):
        # 'register' sends nothing to registered phones, 'reset_password' sends
        # nothing to unknown phones. Login codes are always sent (auto-register).
        skip = (purpose == OTPCode.Purpose.REGISTER and exists) or (
            purpose == OTPCode.Purpose.RESET_PASSWORD and not exists
        )
        if not skip:
            request_otp(phone, purpose, ip_address=get_client_ip(request) or None)
        from django.conf import settings

        return Response(
            {"detail": "If the number is valid, a code has been sent.", "expires_in": settings.OTP_TTL_SECONDS},
            status=status.HTTP_202_ACCEPTED,
        )


class OTPLoginView(AuthView):
    throttle_scope = "otp_verify"

    @extend_schema(tags=["auth"], request=s.OTPLoginSerializer, responses={200: s.TokenPairSerializer})
    def post(self, request):
        ser = s.OTPLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        phone = ser.validated_data["phone"]
        verify_otp(phone, OTPCode.Purpose.LOGIN, ser.validated_data["code"])
        return _token_response(get_or_create_otp_user(phone))


class PasswordLoginView(AuthView):
    @extend_schema(tags=["auth"], request=s.PasswordLoginSerializer, responses={200: s.TokenPairSerializer})
    def post(self, request):
        phone_ser = s.OTPRequestSerializer(data={"phone": request.data.get("phone", "")})
        phone = phone_ser.validated_data["phone"] if phone_ser.is_valid() else None
        if phone and ratelimit.login_locked(phone):
            raise ServiceError(
                "Too many failed attempts for this account. Try again later or log in with a one-time code.",
                code="login_locked", status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        ser = s.PasswordLoginSerializer(data=request.data, context={"request": request})
        if not ser.is_valid():
            if phone and "password" in request.data:
                ratelimit.login_failed(phone)
            raise ValidationError(ser.errors)
        ratelimit.reset("login", phone)
        return _token_response(ser.validated_data["user"])


class RegisterView(AuthView):
    throttle_scope = "otp_verify"

    @extend_schema(tags=["auth"], request=s.RegisterSerializer, responses={201: s.TokenPairSerializer})
    def post(self, request):
        ser = s.RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if User.objects.filter(phone=data["phone"]).exists():
            raise ServiceError("This phone is already registered.", code="phone_registered")
        verify_otp(data["phone"], OTPCode.Purpose.REGISTER, data["code"])
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    phone=data["phone"],
                    password=data["password"],
                    first_name=data.get("first_name", ""),
                    last_name=data.get("last_name", ""),
                )
                mark_phone_verified(user)
        except IntegrityError as exc:  # concurrent registration of the same phone
            raise ConflictError("This phone is already registered.", code="phone_registered") from exc
        return _token_response(user, status.HTTP_201_CREATED)


class PasswordResetView(AuthView):
    throttle_scope = "otp_verify"

    @extend_schema(tags=["auth"], request=s.PasswordResetSerializer, responses={200: dict})
    def post(self, request):
        ser = s.PasswordResetSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        verify_otp(data["phone"], OTPCode.Purpose.RESET_PASSWORD, data["code"])
        user = User.objects.filter(phone=data["phone"], is_active=True).first()
        if user is None:
            raise ServiceError("Invalid or expired code.", code="otp_invalid")
        set_password_and_revoke_tokens(user, data["new_password"])
        mark_phone_verified(user)
        return Response({"detail": "Password updated. Please log in again."})


class LogoutView(APIView):
    @extend_schema(tags=["auth"], request=s.LogoutSerializer, responses={205: None})
    def post(self, request):
        ser = s.LogoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            token = RefreshToken(ser.validated_data["refresh"])
            if str(token.get("user_id")) != str(request.user.pk):
                raise ServiceError("Token does not belong to this user.", code="token_invalid")
            token.blacklist()
        except TokenError as exc:
            raise ServiceError("Invalid token.", code="token_invalid") from exc
        return Response(status=status.HTTP_205_RESET_CONTENT)


class RefreshView(TokenRefreshView):
    serializer_class = s.SafeTokenRefreshSerializer

    @extend_schema(tags=["auth"])
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = s.UserSerializer

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    @extend_schema(tags=["account"], request=s.ChangePasswordSerializer, responses={200: dict})
    def post(self, request):
        ser = s.ChangePasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        set_password_and_revoke_tokens(request.user, ser.validated_data["new_password"])
        # Old sessions are revoked; hand this client a fresh token pair.
        return _token_response(request.user)


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = s.AddressSerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        return Address.objects.filter(user=self.request.user)

    @transaction.atomic
    def perform_create(self, serializer):
        has_any = Address.objects.filter(user=self.request.user).exists()
        is_default = serializer.validated_data.get("is_default") or not has_any
        if is_default:
            Address.objects.filter(user=self.request.user, is_default=True).update(is_default=False)
        serializer.save(user=self.request.user, is_default=is_default)

    @transaction.atomic
    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default"):
            Address.objects.filter(user=self.request.user, is_default=True).exclude(
                pk=serializer.instance.pk
            ).update(is_default=False)
        serializer.save()


# --- admin -----------------------------------------------------------------
class AdminUserViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                       mixins.UpdateModelMixin, viewsets.GenericViewSet):
    queryset = User.objects.prefetch_related("groups").order_by("-date_joined")
    permission_classes = [AdminModelPermission]
    filterset_fields = ["is_active", "is_staff", "groups__name"]
    search_fields = ["phone", "first_name", "last_name", "email"]
    ordering_fields = ["date_joined", "last_login", "phone"]

    def get_serializer_class(self):
        return s.AdminUserCreateSerializer if self.action == "create" else s.AdminUserSerializer


class AdminRoleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Group.objects.prefetch_related("permissions").order_by("name")
    serializer_class = s.GroupSerializer
    permission_classes = [AdminModelPermission]
    pagination_class = None


PAID_STATUSES = ["paid", "processing", "shipped", "delivered"]


class AdminCustomerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Customers (non-staff accounts) with order statistics computed from real orders."""

    permission_classes = [AdminModelPermission]
    queryset = User.objects.filter(is_staff=False)
    filterset_fields = ["is_active"]
    search_fields = ["phone", "first_name", "last_name", "email"]
    ordering_fields = ["date_joined", "last_login", "order_count", "paid_total", "last_order_at"]
    action_perms = {"set_active": ["accounts.change_user"]}

    def get_queryset(self):
        paid = Q(orders__status__in=PAID_STATUSES)
        qs = User.objects.filter(is_staff=False).annotate(
            order_count=Count("orders", distinct=True),
            paid_order_count=Count("orders", filter=paid, distinct=True),
            paid_total=Coalesce(Sum("orders__total", filter=paid), 0),
            last_order_at=Max("orders__created_at"),
        ).order_by("-date_joined")
        if self.action == "retrieve":
            qs = qs.prefetch_related("addresses")
        return qs

    def get_serializer_class(self):
        return s.AdminCustomerDetailSerializer if self.action == "retrieve" else s.AdminCustomerSerializer

    @extend_schema(request=s.SetActiveSerializer, responses=s.AdminCustomerSerializer)
    @action(detail=True, methods=["post"], url_path="set-active")
    def set_active(self, request, pk=None):
        """Disable/enable an account. Disabling also revokes every issued token."""
        customer = self.get_object()
        ser = s.SetActiveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        User.objects.filter(pk=customer.pk).update(is_active=ser.validated_data["is_active"])
        if not ser.validated_data["is_active"]:
            revoke_all_tokens(customer)
        audit.record(request.user, "customer.activate" if ser.validated_data["is_active"] else "customer.deactivate",
                     customer, {"reason": ser.validated_data["reason"]})
        return Response(s.AdminCustomerSerializer(self.get_queryset().get(pk=customer.pk)).data)


class AdminMeView(APIView):
    """The signed-in staff member and their effective permissions (drives the admin UI)."""

    permission_classes = [IsStaff]

    @extend_schema(tags=["admin"], responses=s.AdminMeSerializer)
    def get(self, request):
        return Response(s.AdminMeSerializer(request.user).data)
