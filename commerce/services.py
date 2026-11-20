import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Product, ProductVariant
from commerce.models import Cart, CartItem, Order, OrderItem, OrderStatusHistory
from core.exceptions import ConflictError, ServiceError
from inventory.models import AvailabilityState

logger = logging.getLogger("noribox")
S = Order.Status

# Transitions an administrator may perform manually. Payment-related
# transitions happen exclusively through payments.services.
ADMIN_TRANSITIONS = {
    S.AWAITING_PAYMENT: {S.CANCELLED},
    S.PAYMENT_SUBMITTED: {S.CANCELLED},
    S.PAID: {S.PROCESSING, S.CANCELLED},
    S.PROCESSING: {S.SHIPPED, S.CANCELLED},
    S.SHIPPED: {S.DELIVERED},
    S.DELIVERED: set(),
    S.CANCELLED: set(),
}
CUSTOMER_NOTIFY_STATUSES = {S.PROCESSING, S.SHIPPED, S.DELIVERED, S.CANCELLED}


def sellable_variants():
    return ProductVariant.objects.filter(
        is_active=True,
        product__status=Product.Status.PUBLISHED,
        product__published_at__lte=timezone.now(),
    )


def is_purchasable(variant):
    av = getattr(variant, "availability", None)
    return (
        variant.is_active
        and variant.product.status == Product.Status.PUBLISHED
        and variant.product.published_at is not None
        and variant.product.published_at <= timezone.now()
        and av is not None
        and av.state == AvailabilityState.AVAILABLE
    )


def get_cart(user):
    try:
        with transaction.atomic():
            cart, _ = Cart.objects.get_or_create(user=user)
    except IntegrityError:  # concurrent first request
        cart = Cart.objects.get(user=user)
    return cart


def add_to_cart(user, variant_id):
    variant = sellable_variants().select_related("product", "availability").filter(pk=variant_id).first()
    if variant is None:
        raise ServiceError("Product variant not found.", code="variant_not_found", status_code=404)
    if not is_purchasable(variant):
        raise ConflictError("This item is not available.", code="variant_unavailable")
    cart = get_cart(user)
    if cart.items.count() >= settings.ORDER_MAX_ITEMS:
        raise ServiceError("Cart is full.", code="cart_full")
    try:
        with transaction.atomic():
            item, created = CartItem.objects.get_or_create(cart=cart, variant=variant)
    except IntegrityError:
        item, created = CartItem.objects.get(cart=cart, variant=variant), False
    return item, created


def record_status(order, from_status, to_status, user=None, note=""):
    OrderStatusHistory.objects.create(
        order=order, from_status=from_status or "", to_status=to_status, changed_by=user, note=note[:500]
    )


def set_status(order, to_status, user=None, note=""):
    from_status = order.status
    if from_status == to_status:
        return order
    order.status = to_status
    fields = ["status", "updated_at"]
    if to_status == S.PAID:
        order.paid_at = timezone.now()
        fields.append("paid_at")
    if to_status == S.CANCELLED:
        order.cancelled_at = timezone.now()
        fields.append("cancelled_at")
    order.save(update_fields=fields)
    record_status(order, from_status, to_status, user, note)
    if to_status in CUSTOMER_NOTIFY_STATUSES:
        from notifications.services import notify_order_status

        transaction.on_commit(lambda: notify_order_status(order))
    return order


@transaction.atomic
def create_order(user, address, shipping_method, customer_note=""):
    """Create an order from the user's cart. Does NOT reserve inventory."""
    from shipping.models import Shipment

    cart = Cart.objects.select_for_update().filter(user=user).first()
    if cart is None:
        raise ServiceError("Cart is empty.", code="cart_empty")
    items = list(
        cart.items.select_related("variant__product", "variant__availability")
        .prefetch_related("variant__options", "variant__product__images")
        .order_by("id")
    )
    if not items:
        raise ServiceError("Cart is empty.", code="cart_empty")
    unavailable = [i.variant_id for i in items if not is_purchasable(i.variant)]
    if unavailable:
        raise ConflictError(
            "Some items in your cart are no longer available.",
            code="items_unavailable",
            details={"unavailable_variant_ids": unavailable},
        )
    if address.user_id != user.pk:
        raise ServiceError("Address not found.", code="address_not_found", status_code=404)
    if shipping_method is None or not shipping_method.is_active:
        raise ServiceError("Shipping method is not available.", code="shipping_unavailable")

    subtotal = sum(i.variant.price for i in items)
    shipping_cost = shipping_method.cost_for(subtotal)
    snap = address.snapshot()
    order = Order.objects.create(
        user=user,
        shipping_method=shipping_method,
        shipping_method_name=shipping_method.name,
        subtotal=subtotal,
        shipping_cost=shipping_cost,
        discount_total=0,
        total=subtotal + shipping_cost,
        customer_note=customer_note[:2000],
        **snap,
    )
    order_items = []
    for i in items:
        v = i.variant
        images = list(v.product.images.all())
        order_items.append(
            OrderItem(
                order=order,
                variant=v,
                product_id_snapshot=v.product_id,
                product_name=v.product.name,
                product_slug=v.product.slug,
                sku=v.sku,
                variant_label=v.display_label,
                image_url=images[0].image.url if images else "",
                unit_price=v.price,
                compare_at_price=v.compare_at_price,
                line_total=v.price,
            )
        )
    OrderItem.objects.bulk_create(order_items)
    Shipment.objects.create(order=order, method=shipping_method, method_name=shipping_method.name)
    record_status(order, "", S.AWAITING_PAYMENT, user, "Order created")
    from payments.services import create_payment_for_order

    create_payment_for_order(order)
    cart.items.all().delete()
    return order


@transaction.atomic
def cancel_order(order, user=None, note="", by_admin=False, only_if_status=None):
    """Cancel an order under a row lock.

    ``only_if_status`` restricts cancellation to orders that are *still* in that
    status once locked (used by the scheduled job). Returns the order, or None
    when ``only_if_status`` no longer matches.
    """
    from inventory.services import release_for_order, return_sold_units
    from payments.models import Payment, PaymentReceipt

    order = Order.objects.select_for_update().get(pk=order.pk)
    if only_if_status is not None and order.status != only_if_status:
        return None
    allowed = ADMIN_TRANSITIONS[order.status] if by_admin else {S.CANCELLED} if order.status == S.AWAITING_PAYMENT else set()
    if S.CANCELLED not in allowed:
        raise ConflictError("Order cannot be cancelled in its current state.", code="invalid_transition")
    was_paid = order.status in (S.PAID, S.PROCESSING)
    if was_paid:
        # Units were sold; keep them unavailable until an admin restocks them.
        return_sold_units(order)
        order.refund_status = Order.RefundStatus.PENDING
        order.save(update_fields=["refund_status", "updated_at"])
    else:
        release_for_order(order, reason="order cancelled")
    payments = Payment.objects.select_for_update().filter(order=order).exclude(status=Payment.Status.APPROVED)
    PaymentReceipt.objects.filter(payment__order=order, status=PaymentReceipt.Status.PENDING).update(
        status=PaymentReceipt.Status.CANCELLED, updated_at=timezone.now()
    )
    payments.update(status=Payment.Status.CANCELLED, updated_at=timezone.now())
    if hasattr(order, "shipment"):
        order.shipment.status = order.shipment.Status.CANCELLED
        order.shipment.save(update_fields=["status", "updated_at"])
    if was_paid:
        note = f"{note or 'Cancelled'} (refund pending — record it once the transfer is made)"
    return set_status(order, S.CANCELLED, user, note or "Cancelled")


@transaction.atomic
def record_refund(order, user, reference, note=""):
    """Record a manually executed refund. Never marks a refund without a reference."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.refund_status != Order.RefundStatus.PENDING:
        raise ConflictError("No refund is pending for this order.", code="no_refund_pending")
    if not reference or not reference.strip():
        raise ServiceError("A bank transfer reference is required.", code="reference_required")
    order.refund_status = Order.RefundStatus.REFUNDED
    order.refund_reference = reference.strip()[:100]
    order.refunded_at, order.refunded_by = timezone.now(), user
    order.save(update_fields=["refund_status", "refund_reference", "refunded_at", "refunded_by", "updated_at"])
    record_status(order, order.status, order.status, user, f"Refund recorded: {order.refund_reference} {note}".strip())
    return order


@transaction.atomic
def admin_transition(order, to_status, user, note=""):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if to_status not in ADMIN_TRANSITIONS.get(order.status, set()):
        raise ConflictError(
            f"Cannot change status from {order.status} to {to_status}.", code="invalid_transition"
        )
    if to_status == S.CANCELLED:
        return cancel_order(order, user, note, by_admin=True)
    if to_status == S.SHIPPED:
        shipment = order.shipment
        if shipment.status != shipment.Status.SHIPPED:
            shipment.status = shipment.Status.SHIPPED
            shipment.shipped_at = shipment.shipped_at or timezone.now()
            shipment.save(update_fields=["status", "shipped_at", "updated_at"])
    if to_status == S.DELIVERED:
        shipment = order.shipment
        shipment.status = shipment.Status.DELIVERED
        shipment.delivered_at = timezone.now()
        shipment.save(update_fields=["status", "delivered_at", "updated_at"])
    if to_status == S.PROCESSING:
        shipment = order.shipment
        if shipment.status == shipment.Status.PENDING:
            shipment.status = shipment.Status.PREPARING
            shipment.save(update_fields=["status", "updated_at"])
    return set_status(order, to_status, user, note)


def cancel_stale_unpaid_orders(now=None):
    """Scheduled job: cancel orders left awaiting payment beyond UNPAID_ORDER_TTL_HOURS.

    Each order is re-checked under a row lock, so a receipt upload (which locks
    the same row and moves the order to payment_submitted) can never be
    invalidated: whichever transaction locks first wins, and the other sees
    the new state.
    """
    from datetime import timedelta

    from payments.models import PaymentReceipt

    now = now or timezone.now()
    cutoff = now - timedelta(hours=settings.UNPAID_ORDER_TTL_HOURS)
    cancelled = []
    candidates = Order.objects.filter(status=S.AWAITING_PAYMENT, created_at__lt=cutoff).values_list("pk", flat=True)
    for pk in list(candidates):
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=pk).first()
            if order is None or order.status != S.AWAITING_PAYMENT or order.created_at >= cutoff:
                continue
            receipts = PaymentReceipt.objects.filter(payment__order=order)
            # Never cancel while a receipt is pending, or shortly after a rejection
            # (the customer may resubmit).
            if receipts.filter(status=PaymentReceipt.Status.PENDING).exists() or receipts.filter(created_at__gte=cutoff).exists():
                continue
            if cancel_order(order, None, "Automatically cancelled: unpaid", by_admin=True,
                            only_if_status=S.AWAITING_PAYMENT) is not None:
                cancelled.append(order.number)
    return cancelled
