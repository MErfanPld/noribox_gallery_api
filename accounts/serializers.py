from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import Group
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Address, OTPCode, User
from core.utils import normalize_phone


class PhoneField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 20)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            return normalize_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    refresh[TOKEN_VERSION_CLAIM] = user.token_version  # copied into the access token
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


TOKEN_VERSION_CLAIM = "tv"


def token_is_revoked(token, user):
    return int(token.get(TOKEN_VERSION_CLAIM, 0)) != user.token_version


class SafeTokenRefreshSerializer(TokenRefreshSerializer):
    """Also rejects refresh tokens issued before the user's last password change."""

    def validate(self, attrs):
        try:
            token = RefreshToken(attrs["refresh"])
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc
        user = User.objects.filter(pk=token.get("user_id"), is_active=True).first()
        if user is None or token_is_revoked(token, user):
            raise InvalidToken("Token has been revoked.")
        return super().validate(attrs)


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = serializers.SerializerMethodField()

    def get_user(self, obj) -> dict:
        return UserSerializer(obj["user_obj"]).data


class OTPRequestSerializer(serializers.Serializer):
    phone = PhoneField()
    purpose = serializers.ChoiceField(choices=OTPCode.Purpose.choices, default=OTPCode.Purpose.LOGIN)


class OTPLoginSerializer(serializers.Serializer):
    phone = PhoneField()
    code = serializers.RegexField(r"^\d{4,8}$")


class PasswordLoginSerializer(serializers.Serializer):
    phone = PhoneField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(self.context.get("request"), phone=attrs["phone"], password=attrs["password"])
        if user is None:
            raise serializers.ValidationError({"non_field_errors": ["Invalid phone or password."]})
        attrs["user"] = user
        return attrs


class RegisterSerializer(serializers.Serializer):
    phone = PhoneField()
    code = serializers.RegexField(r"^\d{4,8}$")
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)

    def validate(self, attrs):
        password_validation.validate_password(attrs["password"], User(phone=attrs["phone"]))
        return attrs


class PasswordResetSerializer(serializers.Serializer):
    phone = PhoneField()
    code = serializers.RegexField(r"^\d{4,8}$")
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        password_validation.validate_password(attrs["new_password"])
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = self.context["request"].user
        if user.has_usable_password() and not user.check_password(attrs.get("current_password") or ""):
            raise serializers.ValidationError({"current_password": ["Current password is incorrect."]})
        password_validation.validate_password(attrs["new_password"], user)
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class UserSerializer(serializers.ModelSerializer):
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "phone", "first_name", "last_name", "email", "national_code",
            "phone_verified_at", "date_joined", "is_staff", "has_password",
        ]
        read_only_fields = ["id", "phone", "phone_verified_at", "date_joined", "is_staff", "has_password"]

    def get_has_password(self, obj) -> bool:
        return obj.has_usable_password()

    def validate_national_code(self, value):
        if value and (not value.isdigit() or len(value) != 10):
            raise serializers.ValidationError("National code must be 10 digits.")
        return value


class AddressSerializer(serializers.ModelSerializer):
    recipient_phone = PhoneField()

    class Meta:
        model = Address
        fields = [
            "id", "title", "recipient_name", "recipient_phone", "province", "city",
            "address_line", "postal_code", "plaque", "unit", "is_default", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_postal_code(self, value):
        value = value.strip()
        if not value.isdigit() or len(value) != 10:
            raise serializers.ValidationError("Postal code must be 10 digits.")
        return value


# --- admin -----------------------------------------------------------------
class AdminUserSerializer(serializers.ModelSerializer):
    groups = serializers.SlugRelatedField(slug_field="name", many=True, queryset=Group.objects.all(), required=False)

    class Meta:
        model = User
        fields = [
            "id", "phone", "first_name", "last_name", "email", "national_code", "is_active",
            "is_staff", "is_superuser", "groups", "phone_verified_at", "date_joined", "last_login",
        ]
        read_only_fields = ["id", "phone", "is_superuser", "phone_verified_at", "date_joined", "last_login"]

    def validate(self, attrs):
        request = self.context["request"]
        if ("is_staff" in attrs or "groups" in attrs) and not request.user.is_superuser:
            if not request.user.has_perm("accounts.manage_roles"):
                raise PermissionDenied("You cannot change staff status or roles.")
        if self.instance and self.instance.is_superuser and not request.user.is_superuser:
            raise PermissionDenied("Only superusers can modify superusers.")
        return attrs


class AdminUserCreateSerializer(AdminUserSerializer):
    phone = PhoneField()
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)

    class Meta(AdminUserSerializer.Meta):
        fields = AdminUserSerializer.Meta.fields + ["password"]
        read_only_fields = ["id", "is_superuser", "phone_verified_at", "date_joined", "last_login"]

    def validate_phone(self, value):
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("A user with this phone already exists.")
        return value

    def create(self, validated_data):
        groups = validated_data.pop("groups", [])
        password = validated_data.pop("password", None)
        if password:
            password_validation.validate_password(password)
        user = User.objects.create_user(password=password, **validated_data)
        user.groups.set(groups)
        return user


class GroupSerializer(serializers.ModelSerializer):
    permissions = serializers.SlugRelatedField(slug_field="codename", many=True, read_only=True)

    class Meta:
        model = Group
        fields = ["id", "name", "permissions"]


class AdminCustomerSerializer(serializers.ModelSerializer):
    """Customer CRM row. Money totals only count orders paid through an approved payment."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    order_count = serializers.IntegerField(read_only=True)
    paid_order_count = serializers.IntegerField(read_only=True)
    paid_total = serializers.IntegerField(read_only=True)
    last_order_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "phone", "first_name", "last_name", "full_name", "email", "is_active", "phone_verified_at",
                  "date_joined", "last_login", "order_count", "paid_order_count", "paid_total", "last_order_at"]
        read_only_fields = fields


class AdminCustomerDetailSerializer(AdminCustomerSerializer):
    addresses = AddressSerializer(many=True, read_only=True)
    recent_orders = serializers.SerializerMethodField()
    activity = serializers.SerializerMethodField()

    class Meta(AdminCustomerSerializer.Meta):
        fields = AdminCustomerSerializer.Meta.fields + ["national_code", "addresses", "recent_orders", "activity"]
        read_only_fields = fields

    def get_recent_orders(self, obj) -> list:
        return [{"id": o.pk, "number": o.number, "status": o.status, "total": o.total, "created_at": o.created_at,
                 "paid_at": o.paid_at} for o in obj.orders.order_by("-created_at")[:20]]

    def get_activity(self, obj) -> list:
        """Timeline built only from recorded events (registration, orders, receipts)."""
        from payments.models import PaymentReceipt

        events = [{"type": "registered", "at": obj.date_joined}]
        if obj.last_login:
            events.append({"type": "last_login", "at": obj.last_login})
        for o in obj.orders.order_by("-created_at")[:10]:
            events.append({"type": "order_created", "at": o.created_at, "order": o.number})
        for r in PaymentReceipt.objects.filter(uploaded_by=obj).select_related("payment__order").order_by("-created_at")[:10]:
            events.append({"type": "receipt_uploaded", "at": r.created_at, "order": r.payment.order.number, "status": r.status})
        return sorted(events, key=lambda e: e["at"], reverse=True)


class SetActiveSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class AdminMeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    permissions = serializers.SerializerMethodField()
    roles = serializers.SlugRelatedField(source="groups", slug_field="name", many=True, read_only=True)

    class Meta:
        model = User
        fields = ["id", "phone", "first_name", "last_name", "full_name", "is_staff", "is_superuser", "roles", "permissions"]

    def get_permissions(self, obj) -> list:
        if obj.is_superuser:
            return ["*"]
        return sorted(obj.get_all_permissions())
