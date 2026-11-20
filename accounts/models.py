import hashlib
import hmac

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from core.utils import normalize_phone


def validate_phone(value):
    try:
        normalize_phone(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra):
        phone = normalize_phone(phone)
        user = self.model(phone=phone, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra)

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(max_length=11, unique=True, validators=[validate_phone])
    first_name = models.CharField(max_length=150, blank=True, default="")
    last_name = models.CharField(max_length=150, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    national_code = models.CharField(max_length=10, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    token_version = models.PositiveIntegerField(
        default=0, help_text="Embedded in JWTs ('tv'); bumped on password change/reset to revoke all tokens."
    )
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["-date_joined"]
        permissions = [("manage_roles", "Can assign staff status and roles")]

    def __str__(self):
        return self.phone

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name or self.phone


class Address(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses")
    title = models.CharField(max_length=100, blank=True, default="")
    recipient_name = models.CharField(max_length=200)
    recipient_phone = models.CharField(max_length=11, validators=[validate_phone])
    province = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    address_line = models.TextField()
    postal_code = models.CharField(max_length=10)
    plaque = models.CharField(max_length=20, blank=True, default="")
    unit = models.CharField(max_length=20, blank=True, default="")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(is_default=True), name="uniq_default_address_per_user"
            )
        ]

    def __str__(self):
        return f"{self.recipient_name} - {self.city}"

    def snapshot(self):
        return {
            "recipient_name": self.recipient_name,
            "recipient_phone": self.recipient_phone,
            "province": self.province,
            "city": self.city,
            "address_line": self.address_line,
            "postal_code": self.postal_code,
            "plaque": self.plaque,
            "unit": self.unit,
        }


class OTPCode(models.Model):
    class Purpose(models.TextChoices):
        LOGIN = "login", "Login"
        REGISTER = "register", "Register"
        RESET_PASSWORD = "reset_password", "Reset password"

    phone = models.CharField(max_length=11, db_index=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["phone", "purpose", "created_at"])]

    @staticmethod
    def hash_code(phone, purpose, code):
        msg = f"{phone}:{purpose}:{code}".encode()
        return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()

    def matches(self, code):
        return hmac.compare_digest(self.code_hash, self.hash_code(self.phone, self.purpose, code))

    @property
    def is_usable(self):
        return (
            self.consumed_at is None
            and self.invalidated_at is None
            and self.expires_at > timezone.now()
            and self.attempts < settings.OTP_MAX_ATTEMPTS
        )
