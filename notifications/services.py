import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from notifications.models import SMSLog, SMSTemplate
from notifications.providers import get_provider

logger = logging.getLogger("noribox")

DEFAULT_TEMPLATES = {
    "otp": "کد تایید نوری‌باکس: {code}",
    "receipt_uploaded_admin": "رسید پرداخت جدید برای سفارش {order_number} به مبلغ {amount} تومان ثبت شد.{conflict}",
    "payment_approved": "پرداخت سفارش {order_number} تایید شد. از خرید شما سپاسگزاریم.",
    "payment_rejected": "رسید پرداخت سفارش {order_number} رد شد: {reason}. لطفا رسید معتبر بارگذاری کنید.",
    "order_status_processing": "سفارش {order_number} در حال آماده‌سازی است.",
    "order_status_shipped": "سفارش {order_number} ارسال شد. کد رهگیری: {tracking_code}",
    "order_status_delivered": "سفارش {order_number} تحویل شد.",
    "order_status_cancelled": "سفارش {order_number} لغو شد.",
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render(code, context):
    template = SMSTemplate.objects.filter(code=code).first()
    if template is not None and not template.is_active:
        return template, None
    body = template.body if template else DEFAULT_TEMPLATES.get(code)
    if body is None:
        return template, None
    return template, body.format_map(_SafeDict(context))


_ATTEMPT_FIELDS = ["provider", "attempts", "status", "last_error", "next_retry_at", "sent_at",
                   "provider_message_id", "updated_at"]


def _attempt(log, template=None, context=None, message=None):
    """Send once. ``message`` overrides the stored body (used for secrets that are never stored)."""
    provider = get_provider()
    log.provider = provider.name
    log.attempts += 1
    try:
        result = provider.send(log.phone, message or log.message, template=template, context=context or log.context)
    except Exception as exc:  # noqa: BLE001 - providers may raise anything
        log.last_error = f"{exc.__class__.__name__}: {exc}"[:2000]
        if log.attempts >= settings.SMS_MAX_ATTEMPTS or message is not None:
            log.status, log.next_retry_at = SMSLog.Status.GAVE_UP, None
        else:
            log.status = SMSLog.Status.FAILED
            delay = settings.SMS_RETRY_BASE_SECONDS * (2 ** (log.attempts - 1))
            log.next_retry_at = timezone.now() + timedelta(seconds=delay)
        logger.warning("SMS %s to %s failed (attempt %s)", log.template_code, log.phone, log.attempts)
    else:
        log.status, log.sent_at, log.next_retry_at = SMSLog.Status.SENT, timezone.now(), None
        log.provider_message_id = result.message_id[:128]
        log.last_error = ""
    log.save(update_fields=_ATTEMPT_FIELDS)
    return log


def send_sms(code, phone, context=None, sensitive=False, deferred=None):
    """Render + send a templated SMS, logging every attempt. Never raises.

    ``sensitive`` (OTP): sent synchronously, body and context are never written
    to the database or logs, and it is never retried.
    ``deferred`` (default ``SMS_ASYNC_NOTIFICATIONS``): queue the message for
    ``run_scheduled_jobs``/``process_sms_queue`` instead of calling the provider
    inside the request.
    """
    context = {k: ("" if v is None else str(v)) for k, v in (context or {}).items()}
    if deferred is None:
        deferred = settings.SMS_ASYNC_NOTIFICATIONS and not sensitive
    try:
        template, message = render(code, context)
        if not settings.SMS_ENABLED or message is None:
            return SMSLog.objects.create(
                phone=phone, template_code=code, message="" if sensitive else (message or ""),
                status=SMSLog.Status.SKIPPED,
            )
        log = SMSLog.objects.create(
            phone=phone,
            template_code=code,
            message="[redacted]" if sensitive else message,
            context={} if sensitive else context,
            status=SMSLog.Status.QUEUED,
            next_retry_at=timezone.now() if deferred and not sensitive else None,
        )
        if sensitive:
            return _attempt(log, template, context, message=message)
        if deferred:
            return log
        return _attempt(log, template, context)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to queue SMS %s", code)
        return None


def process_retries(now=None, limit=200):
    """Send queued messages and retry failed ones whose backoff has elapsed."""
    from django.db import connection, transaction

    now = now or timezone.now()
    count = 0
    due = SMSLog.objects.filter(
        status__in=[SMSLog.Status.QUEUED, SMSLog.Status.FAILED], next_retry_at__lte=now
    ).exclude(message="[redacted]").order_by("next_retry_at")
    for pk in list(due.values_list("pk", flat=True)[:limit]):
        with transaction.atomic():
            qs = SMSLog.objects.filter(pk=pk, status__in=[SMSLog.Status.QUEUED, SMSLog.Status.FAILED])
            if connection.features.has_select_for_update_skip_locked:
                qs = qs.select_for_update(skip_locked=True)
            log = qs.first()
            if log is None:
                continue
            _attempt(log, SMSTemplate.objects.filter(code=log.template_code).first())
            count += 1
    return count


def admin_phones():
    phones = list(settings.SMS_ADMIN_PHONES)
    try:
        from cms.models import StoreSettings

        phones += StoreSettings.load().admin_phone_list()
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(phones))


def notify_admins_receipt_uploaded(receipt):
    order = receipt.payment.order
    conflict = " (تداخل موجودی - نیاز به بررسی)" if receipt.reservation_status == "conflict" else ""
    for phone in admin_phones():
        send_sms("receipt_uploaded_admin", phone, {"order_number": order.number, "amount": order.total, "conflict": conflict})


def notify_payment_result(order, approved, reason=""):
    code = "payment_approved" if approved else "payment_rejected"
    send_sms(code, order.user.phone, {"order_number": order.number, "reason": reason})


def notify_order_status(order):
    shipment = getattr(order, "shipment", None)
    send_sms(
        f"order_status_{order.status}",
        order.user.phone,
        {"order_number": order.number, "tracking_code": getattr(shipment, "tracking_code", "")},
    )
