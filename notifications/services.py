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


def _attempt(log, template=None, context=None):
    provider = get_provider()
    log.provider = provider.name
    log.attempts += 1
    try:
        result = provider.send(log.phone, log.message, template=template, context=context or log.context)
    except Exception as exc:  # noqa: BLE001 - providers may raise anything
        log.last_error = str(exc)[:2000]
        if log.attempts >= settings.SMS_MAX_ATTEMPTS or log.template_code == "otp":
            log.status, log.next_retry_at = SMSLog.Status.GAVE_UP, None
        else:
            log.status = SMSLog.Status.FAILED
            delay = settings.SMS_RETRY_BASE_SECONDS * (2 ** (log.attempts - 1))
            log.next_retry_at = timezone.now() + timedelta(seconds=delay)
        logger.warning("SMS %s to %s failed (attempt %s): %s", log.template_code, log.phone, log.attempts, exc)
    else:
        log.status, log.sent_at, log.next_retry_at = SMSLog.Status.SENT, timezone.now(), None
        log.provider_message_id = result.message_id[:128]
        log.last_error = ""
    log.save()
    return log


def send_sms(code, phone, context=None, sensitive=False):
    """Render + send a templated SMS, logging every attempt. Never raises."""
    context = {k: ("" if v is None else str(v)) for k, v in (context or {}).items()}
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
            # Never persist secrets such as OTP codes.
            message="[redacted]" if sensitive else message,
            context={} if sensitive else context,
        )
        if sensitive:
            log.message = message  # in-memory only for this single attempt
            _attempt(log, template, context)
            SMSLog.objects.filter(pk=log.pk).update(message="[redacted]")
            return log
        return _attempt(log, template, context)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to queue SMS %s", code)
        return None


def process_retries(now=None, limit=200):
    now = now or timezone.now()
    due = SMSLog.objects.filter(status=SMSLog.Status.FAILED, next_retry_at__lte=now).order_by("next_retry_at")[:limit]
    count = 0
    for log in due:
        template = SMSTemplate.objects.filter(code=log.template_code).first()
        _attempt(log, template)
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
