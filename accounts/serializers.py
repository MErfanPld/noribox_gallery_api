from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import Group
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
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
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


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
