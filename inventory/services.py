"""Transaction-safe reservation workflows.

Locking order: VariantAvailability rows are always locked with
``SELECT ... FOR UPDATE`` ordered by variant_id, so concurrent reservations
for overlapping variant sets cannot deadlock. The partial unique constraint
``uniq_active_reservation_per_variant`` is the final guarantee.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.exceptions import ConflictError, ServiceError
from inventory.models import AvailabilityState, Reservation, VariantAvailability

logger = logging.getLogger("noribox")


class ReservationConflict(ConflictError):
    default_code = "reservation_conflict"
    default_detail = "One or more items are no longer available."


def ensure_availability(variant):
    obj, _ = VariantAvailability.objects.get_or_create(variant=variant)
    return obj


def _lock(variant_ids):
    rows = list(
        VariantAvailability.objects.select_for_update().filter(variant_id__in=variant_ids).order_by("variant_id")
    )
    missing = set(variant_ids) - {r.variant_id for r in rows}
    if missing:
        for vid in sorted(missing):
            VariantAvailability.objects.get_or_create(variant_id=vid)
        rows = list(
            VariantAvailability.objects.select_for_update().filter(variant_id__in=variant_ids).order_by("variant_id")
        )
    return {r.variant_id: r for r in rows}


def reserve_for_order(order):
    """Reserve every variant of ``order`` atomically (all or nothing).

    Raises ReservationConflict (and reserves nothing) if any variant is not
    available. Idempotent for variants already reserved by this order.
    """
    variant_ids = sorted({vid for vid in order.items.values_list("variant_id", flat=True) if vid})
    if not variant_ids or len(variant_ids) != order.items.count():
        raise ReservationConflict("Order contains items that no longer exist.", details={"unavailable": []})
    expires_at = timezone.now() + timedelta(hours=settings.RESERVATION_TTL_HOURS)
    try:
        with transaction.atomic():
            rows = _lock(variant_ids)
            own = set(
                Reservation.objects.filter(order=order, status=Reservation.Status.ACTIVE).values_list("variant_id", flat=True)
            )
            conflicts = [
                vid for vid in variant_ids if vid not in own and rows[vid].state != AvailabilityState.AVAILABLE
            ]
            if conflicts:
                raise ReservationConflict(details={"unavailable_variant_ids": conflicts})
            created = []
            for vid in variant_ids:
                if vid in own:
                    continue
                created.append(Reservation(variant_id=vid, order=order, expires_at=expires_at))
                rows[vid].state = AvailabilityState.RESERVED
                rows[vid].save(update_fields=["state", "updated_at"])
            Reservation.objects.bulk_create(created)
            Reservation.objects.filter(order=order, status=Reservation.Status.ACTIVE).update(expires_at=expires_at)
    except IntegrityError as exc:  # concurrent winner on the unique constraint
        raise ReservationConflict(details={"unavailable_variant_ids": variant_ids}) from exc
    return list(Reservation.objects.filter(order=order, status=Reservation.Status.ACTIVE))


def _close(order, new_status, reason, final_state=None):
    now = timezone.now()
    with transaction.atomic():
        reservations = list(
            Reservation.objects.select_for_update().filter(order=order, status=Reservation.Status.ACTIVE)
        )
        if not reservations:
            return 0
        rows = _lock([r.variant_id for r in reservations])
        for r in reservations:
            row = rows[r.variant_id]
            if final_state == AvailabilityState.UNAVAILABLE:
                row.state = AvailabilityState.UNAVAILABLE
                row.sold = True
                row.note = f"Sold via order {order.number}"
                row.save(update_fields=["state", "sold", "note", "updated_at"])
            elif row.state == AvailabilityState.RESERVED:
                # Only revert reserved -> available; an administrator's
                # independent 'unavailable' decision is preserved.
                row.state = AvailabilityState.AVAILABLE
                row.save(update_fields=["state", "updated_at"])
            r.status, r.closed_at, r.close_reason = new_status, now, reason[:255]
        Reservation.objects.bulk_update(reservations, ["status", "closed_at", "close_reason"])
    return len(reservations)


def release_for_order(order, reason="released"):
    return _close(order, Reservation.Status.RELEASED, reason)


def convert_for_order(order):
    """Turn the order's reservations into a completed sale (variants become unavailable)."""
    active = Reservation.objects.filter(order=order, status=Reservation.Status.ACTIVE).count()
    if active != order.items.count():
        raise ReservationConflict("Order is not fully reserved.", code="not_reserved")
    return _close(order, Reservation.Status.CONVERTED, "payment approved", AvailabilityState.UNAVAILABLE)


def expire_reservations(now=None):
    """Expire overdue reservations (scheduled job). Returns affected order ids."""
    now = now or timezone.now()
    from commerce.models import Order

    order_ids = set(
        Reservation.objects.filter(status=Reservation.Status.ACTIVE, expires_at__lt=now).values_list("order_id", flat=True)
    )
    for order in Order.objects.filter(pk__in=order_ids):
        _close(order, Reservation.Status.EXPIRED, "reservation expired")
        order.flag_for_review("Reservation expired before payment review.")
        logger.info("Reservation expired for order %s", order.number)
    return order_ids


def set_availability(variant, state, user=None, note=""):
    """Administrative availability change. 'reserved' is only set by reservations."""
    if state == AvailabilityState.RESERVED:
        raise ServiceError("Reserved state is managed by the reservation workflow.", code="invalid_state")
    with transaction.atomic():
        row = _lock([variant.pk])[variant.pk]
        has_active = Reservation.objects.filter(variant=variant, status=Reservation.Status.ACTIVE).exists()
        if state == AvailabilityState.AVAILABLE and has_active:
            raise ConflictError("Variant has an active reservation; release it first.", code="variant_reserved")
        if state == AvailabilityState.AVAILABLE and row.sold:
            raise ConflictError("Variant was sold. Create a new variant for a new unit.", code="variant_sold")
        row.state, row.updated_by, row.note = state, user, note[:500]
        row.save(update_fields=["state", "updated_by", "note", "updated_at"])
    return row
