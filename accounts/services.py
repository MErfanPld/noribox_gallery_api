import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from accounts.models import OTPCode, User
from core.exceptions import ServiceError

logger = logging.getLogger("noribox")


def _generate_code():
    length = settings.OTP_LENGTH
    return str(secrets.randbelow(10**length)).zfill(length)


def request_otp(phone, purpose, ip_address=None):
    """Create a new OTP (invalidating older ones) and send it by SMS."""
    now = timezone.now()
    recent = OTPCode.objects.filter(phone=phone, purpose=purpose)
    last = recent.first()
    if last and (now - last.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - int((now - last.created_at).total_seconds())
        raise ServiceError(
            "Please wait before requesting a new code.",
            code="otp_cooldown",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details={"retry_after_seconds": max(wait, 1)},
        )
    if OTPCode.objects.filter(phone=phone, created_at__gte=now - timedelta(hours=1)).count() >= settings.OTP_MAX_PER_HOUR:
        raise ServiceError(
            "Too many codes requested. Try again later.",
            code="otp_rate_limited",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    code = _generate_code()
    with transaction.atomic():
        recent.filter(consumed_at__isnull=True, invalidated_at__isnull=True).update(invalidated_at=now)
        otp = OTPCode.objects.create(
            phone=phone,
            purpose=purpose,
            code_hash=OTPCode.hash_code(phone, purpose, code),
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
            ip_address=ip_address,
        )
    from notifications.services import send_sms

    transaction.on_commit(
        lambda: send_sms("otp", phone, {"code": code}, sensitive=True)
    )
    return otp


def verify_otp(phone, purpose, code):
    """Validate and consume an OTP. Counts failed attempts. Raises ServiceError."""
    invalid = ServiceError("Invalid or expired code.", code="otp_invalid")
    with transaction.atomic():
        otp = (
            OTPCode.objects.select_for_update()
            .filter(phone=phone, purpose=purpose, consumed_at__isnull=True, invalidated_at__isnull=True)
            .first()
        )
        if otp is None or not otp.is_usable:
            raise invalid
        if not otp.matches(str(code)):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            matched = False
        else:
            otp.consumed_at = timezone.now()
            otp.save(update_fields=["consumed_at"])
            matched = True
    # Raise outside the atomic block so the failed attempt counter is committed.
    if not matched:
        raise invalid
    return otp


def mark_phone_verified(user):
    if user.phone_verified_at is None:
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at"])


def revoke_all_tokens(user):
    """Blacklist every outstanding refresh token and invalidate issued access tokens."""
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    from django.db.models import F

    type(user).objects.filter(pk=user.pk).update(token_version=F("token_version") + 1)
    user.refresh_from_db(fields=["token_version"])
    for token in OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True):
        BlacklistedToken.objects.get_or_create(token=token)


@transaction.atomic
def set_password_and_revoke_tokens(user, raw_password):
    user.set_password(raw_password)
    user.save(update_fields=["password"])
    revoke_all_tokens(user)


def get_or_create_otp_user(phone):
    from django.db import IntegrityError

    user = User.objects.filter(phone=phone).first()
    if user is None:
        if not settings.OTP_LOGIN_AUTO_REGISTER:
            raise ServiceError("Invalid or expired code.", code="otp_invalid")
        try:
            with transaction.atomic():
                user = User.objects.create_user(phone=phone)
        except IntegrityError:  # concurrent first login
            user = User.objects.get(phone=phone)
    if not user.is_active:
        raise ServiceError("Account disabled.", code="account_disabled", status_code=403)
    mark_phone_verified(user)
    return user
