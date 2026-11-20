"""Manual bank-transfer payments.

Receipt upload != approval. Upload stores the receipt (always preserved) and
then attempts an atomic reservation. A failed reservation flags the order for
administrative review instead of discarding the receipt.
"""
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from commerce.models import Order
from commerce.services import set_status
from core.exceptions import ConflictError, ServiceError
from core.files import validate_upload
from inventory.services import ReservationConflict, convert_for_order, release_for_order, reserve_for_order
from payments.models import Payment, PaymentMethod, PaymentReceipt, PaymentReview

logger = logging.getLogger("noribox")
S = Order.Status


def create_payment_for_order(order):
    method = (
        PaymentMethod.objects.filter(is_active=True, kind=PaymentMethod.Kind.BANK_TRANSFER)
        .order_by("-is_default", "sort_order", "id")
        .first()
    )
    return Payment.objects.create(order=order, method=method, amount=order.total)


def current_payment(order):
    return order.payments.exclude(status=Payment.Status.CANCELLED).order_by("-created_at").first()


def upload_receipt(order, user, uploaded, reference_number="", payer_card_last4="", paid_at=None,
                   declared_amount=None, customer_note=""):
    try:
        ext = validate_upload(uploaded, settings.RECEIPT_ALLOWED_EXTENSIONS, settings.RECEIPT_MAX_SIZE_BYTES)
    except ValidationError as exc:
        raise ServiceError(exc.messages[0], code="invalid_file") from exc
    content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "pdf": "application/pdf"}.get(ext, "application/octet-stream")
    sha = PaymentReceipt.digest(uploaded)

    # Step 1: persist the receipt (committed independently of the reservation).
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.user_id != user.pk:
            raise ServiceError("Order not found.", code="not_found", status_code=404)
        if order.status != S.AWAITING_PAYMENT:
            raise ConflictError("Receipts can only be uploaded for orders awaiting payment.", code="invalid_order_state")
        payment = current_payment(order)
        if payment is None:
            payment = create_payment_for_order(order)
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status not in (Payment.Status.AWAITING_RECEIPT, Payment.Status.REJECTED):
            raise ConflictError("A receipt is already under review.", code="receipt_pending")
        receipt = PaymentReceipt(
            payment=payment,
            uploaded_by=user,
            original_filename=uploaded.name[:255],
            content_type=content_type,
            size=uploaded.size,
            sha256=sha,
            reference_number=reference_number[:100],
            payer_card_last4=payer_card_last4,
            paid_at=paid_at,
            declared_amount=declared_amount,
            customer_note=customer_note[:1000],
        )
        receipt.file.save(uploaded.name, uploaded, save=False)
        try:
            receipt.save()
        except Exception:
            receipt.file.delete(save=False)
            raise
        payment.status = Payment.Status.UNDER_REVIEW
        payment.save(update_fields=["status", "updated_at"])
        set_status(order, S.PAYMENT_SUBMITTED, user, "Receipt uploaded")

    # Step 2: atomic all-or-nothing reservation attempt.
    try:
        reserve_for_order(order)
        receipt.reservation_status = PaymentReceipt.ReservationStatus.RESERVED
        receipt.reservation_detail = {}
    except ReservationConflict as exc:
        receipt.reservation_status = PaymentReceipt.ReservationStatus.CONFLICT
        receipt.reservation_detail = exc.details
        order.refresh_from_db()
        order.flag_for_review("Inventory reservation failed after receipt upload.")
        logger.warning("Reservation conflict for order %s: %s", order.number, exc.details)
    receipt.save(update_fields=["reservation_status", "reservation_detail", "updated_at"])

    from notifications.services import notify_admins_receipt_uploaded

    transaction.on_commit(lambda: notify_admins_receipt_uploaded(receipt))
    return receipt


def _notify_customer(order, approved, reason=""):
    from notifications.services import notify_payment_result

    transaction.on_commit(lambda: notify_payment_result(order, approved, reason))


@transaction.atomic
def approve_receipt(receipt, reviewer, note=""):
    receipt = PaymentReceipt.objects.select_for_update().select_related("payment__order").get(pk=receipt.pk)
    payment = Payment.objects.select_for_update().get(pk=receipt.payment_id)
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if receipt.status != PaymentReceipt.Status.PENDING or payment.status != Payment.Status.UNDER_REVIEW:
        raise ConflictError("Receipt is not pending review.", code="receipt_not_pending")
    if order.status != S.PAYMENT_SUBMITTED:
        raise ConflictError("Order is not awaiting payment review.", code="invalid_order_state")
    try:
        with transaction.atomic():
            reserve_for_order(order)  # idempotent for already-reserved items
            convert_for_order(order)
    except ReservationConflict as exc:
        raise ConflictError(
            "Cannot approve: some items could not be reserved. Resolve inventory or reject the payment.",
            code="reservation_conflict",
            details=exc.details,
        ) from exc
    now = timezone.now()
    receipt.status = PaymentReceipt.Status.APPROVED
    receipt.reservation_status = PaymentReceipt.ReservationStatus.RESERVED
    receipt.save(update_fields=["status", "reservation_status", "updated_at"])
    PaymentReview.objects.create(receipt=receipt, reviewer=reviewer, decision=PaymentReview.Decision.APPROVED, reason=note)
    payment.status, payment.approved_at = Payment.Status.APPROVED, now
    payment.save(update_fields=["status", "approved_at", "updated_at"])
    order.needs_review, order.review_reason = False, ""
    order.save(update_fields=["needs_review", "review_reason", "updated_at"])
    set_status(order, S.PAID, reviewer, note or "Payment approved")
    _notify_customer(order, True)
    return receipt


@transaction.atomic
def reject_receipt(receipt, reviewer, reason):
    if not reason or not reason.strip():
        raise ServiceError("A rejection reason is required.", code="reason_required")
    receipt = PaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
    payment = Payment.objects.select_for_update().get(pk=receipt.payment_id)
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if receipt.status != PaymentReceipt.Status.PENDING or payment.status != Payment.Status.UNDER_REVIEW:
        raise ConflictError("Receipt is not pending review.", code="receipt_not_pending")
    release_for_order(order, reason="payment rejected")
    receipt.status = PaymentReceipt.Status.REJECTED
    receipt.save(update_fields=["status", "updated_at"])
    PaymentReview.objects.create(receipt=receipt, reviewer=reviewer, decision=PaymentReview.Decision.REJECTED, reason=reason)
    payment.status = Payment.Status.REJECTED
    payment.save(update_fields=["status", "updated_at"])
    order.needs_review, order.review_reason = False, ""
    order.save(update_fields=["needs_review", "review_reason", "updated_at"])
    set_status(order, S.AWAITING_PAYMENT, reviewer, f"Payment rejected: {reason}")
    _notify_customer(order, False, reason)
    return receipt
