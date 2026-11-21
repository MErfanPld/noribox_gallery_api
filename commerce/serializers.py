from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from catalog.serializers import PublicVariantSerializer
from commerce.models import Cart, CartItem, Order, OrderItem, OrderStatusHistory, OrderNote
from payments.serializers import PaymentSerializer
from shipping.serializers import ShipmentSerializer


class CartVariantSerializer(PublicVariantSerializer):
    product_name = serializers.CharField(source="product.name")
    product_slug = serializers.CharField(source="product.slug")
    image = serializers.SerializerMethodField()

    class Meta(PublicVariantSerializer.Meta):
        fields = PublicVariantSerializer.Meta.fields + ["product_name", "product_slug", "image"]

    def get_image(self, obj) -> str | None:
        images = list(obj.product.images.all())
        if not images:
            return None
        request = self.context.get("request")
        url = images[0].image.url
        return request.build_absolute_uri(url) if request else url


class CartItemSerializer(serializers.ModelSerializer):
    variant = CartVariantSerializer(read_only=True)
    is_purchasable = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ["id", "variant", "quantity", "is_purchasable", "created_at"]

    def get_is_purchasable(self, obj) -> bool:
        from commerce.services import is_purchasable

        return is_purchasable(obj.variant)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ["id", "items", "item_count", "subtotal", "updated_at"]

    def get_subtotal(self, obj) -> int:
        return sum(i.variant.price for i in obj.items.all())

    def get_item_count(self, obj) -> int:
        return len(obj.items.all())


class AddCartItemSerializer(serializers.Serializer):
    variant_id = serializers.IntegerField(min_value=1)


class OrderItemSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    def get_image_url(self, obj) -> str | None:
        url = obj.image_url
        if not url:
            return None
        if not url.startswith(("/", "http://", "https://")):  # legacy rows stored the storage name
            from django.conf import settings

            url = settings.MEDIA_URL.rstrip("/") + "/" + url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request and url.startswith("/") else url

    class Meta:
        model = OrderItem
        fields = ["id", "variant", "product_name", "product_slug", "sku", "variant_label", "image_url",
                  "unit_price", "compare_at_price", "quantity", "line_total"]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusHistory
        fields = ["from_status", "to_status", "note", "created_at"]


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(source="items.count", read_only=True)

    class Meta:
        model = Order
        fields = ["number", "status", "total", "item_count", "created_at", "paid_at"]


ADDRESS_FIELDS = ["recipient_name", "recipient_phone", "province", "city", "address_line", "postal_code", "plaque", "unit"]


class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    payment_expires_at = serializers.SerializerMethodField(
        help_text="Deadline for uploading a receipt (server-computed); null unless awaiting payment."
    )
    history = OrderStatusHistorySerializer(source="status_history", many=True, read_only=True)
    shipment = ShipmentSerializer(read_only=True)
    payment = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["number", "status", *ADDRESS_FIELDS, "shipping_method_name", "subtotal", "shipping_cost",
                  "discount_total", "total", "customer_note", "items", "shipment", "payment", "history",
                  "created_at", "paid_at", "cancelled_at", "payment_expires_at"]

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_payment_expires_at(self, obj):
        from commerce.services import payment_deadline

        deadline = payment_deadline(obj)
        return serializers.DateTimeField().to_representation(deadline) if deadline else None

    @extend_schema_field(PaymentSerializer(allow_null=True))
    def get_payment(self, obj):
        from payments.services import current_payment

        payment = current_payment(obj)
        return PaymentSerializer(payment, context=self.context).data if payment else None


class CreateOrderSerializer(serializers.Serializer):
    address_id = serializers.IntegerField()
    shipping_method_id = serializers.IntegerField()
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class OrderNoteSerializer(serializers.ModelSerializer):
    author_phone = serializers.CharField(source="author.phone", read_only=True, default=None)
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderNote
        fields = ["id", "body", "author_phone", "author_name", "created_at"]
        read_only_fields = ["id", "author_phone", "author_name", "created_at"]

    def get_author_name(self, obj) -> str:
        return obj.author.get_full_name() if obj.author else ""

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("یادداشت خالی است.")
        return value.strip()


class AdminOrderListSerializer(serializers.ModelSerializer):
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    customer_name = serializers.SerializerMethodField()
    item_count = serializers.IntegerField(read_only=True)
    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "number", "status", "user", "user_phone", "customer_name", "recipient_name", "total",
                  "item_count", "payment_status", "needs_review", "refund_status", "created_at", "paid_at"]

    def get_customer_name(self, obj) -> str:
        return obj.user.get_full_name() if obj.user_id else ""

    def get_payment_status(self, obj) -> str | None:
        payments = list(obj.payments.all())
        return payments[-1].status if payments else None


class AdminOrderSerializer(OrderDetailSerializer):
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    customer_name = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    notes = OrderNoteSerializer(many=True, read_only=True)
    allowed_transitions = serializers.SerializerMethodField()

    class Meta(OrderDetailSerializer.Meta):
        fields = ["id", "user", "user_phone", "customer_name", *OrderDetailSerializer.Meta.fields, "admin_note",
                  "needs_review", "review_reason", "refund_status", "refund_reference", "refunded_at", "updated_at",
                  "notes", "allowed_transitions"]
        read_only_fields = [f for f in fields if f not in ("admin_note",)]

    def get_customer_name(self, obj) -> str:
        return obj.user.get_full_name() if obj.user_id else ""

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_payment(self, obj):
        from payments.serializers import AdminPaymentSerializer
        from payments.services import current_payment

        payment = current_payment(obj)
        return AdminPaymentSerializer(payment, context=self.context).data if payment else None

    def get_allowed_transitions(self, obj) -> list:
        from commerce.services import ADMIN_TRANSITIONS

        return sorted(ADMIN_TRANSITIONS.get(obj.status, set()))

    def get_history(self, obj) -> list:
        return [
            {"from_status": h.from_status, "to_status": h.to_status, "note": h.note, "created_at": h.created_at,
             "changed_by": h.changed_by.phone if h.changed_by else None}
            for h in obj.status_history.all()
        ]


class RecordRefundSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100)
    note = serializers.CharField(required=False, allow_blank=True, max_length=300)


class AdminTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


# --- guest order tracking (public, deliberately limited) ---------------------
def mask_name(name: str) -> str:
    return " ".join(f"{part[0]}***" for part in (name or "").split() if part)


class TrackOrderRequestSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=20, trim_whitespace=True)
    phone = serializers.CharField(max_length=20)

    def validate_number(self, value):
        return value.strip().upper()

    def validate_phone(self, value):
        from core.utils import normalize_phone

        try:
            return normalize_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class TrackedOrderItemSerializer(OrderItemSerializer):
    class Meta(OrderItemSerializer.Meta):
        fields = ["product_name", "variant_label", "image_url", "unit_price"]


class TrackedOrderSerializer(serializers.ModelSerializer):
    """Public tracking view: no address line, postal code, phones, receipts, notes or refund data."""

    payment_status = serializers.SerializerMethodField()
    payment_expires_at = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()
    items = TrackedOrderItemSerializer(many=True, read_only=True)
    recipient_name = serializers.SerializerMethodField()
    shipment = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["number", "status", "payment_status", "created_at", "paid_at", "payment_expires_at", "total",
                  "item_count", "items", "recipient_name", "province", "city", "shipping_method_name", "shipment",
                  "timeline"]

    def get_payment_status(self, obj) -> str | None:
        from payments.services import current_payment

        payment = current_payment(obj)
        return payment.status if payment else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_payment_expires_at(self, obj):
        from commerce.services import payment_deadline

        deadline = payment_deadline(obj)
        return serializers.DateTimeField().to_representation(deadline) if deadline else None

    def get_item_count(self, obj) -> int:
        return len(obj.items.all())

    def get_recipient_name(self, obj) -> str:
        return mask_name(obj.recipient_name)

    def get_shipment(self, obj) -> dict | None:
        s = getattr(obj, "shipment", None)
        if s is None:
            return None
        return {"status": s.status, "carrier": s.carrier, "tracking_code": s.tracking_code, "tracking_url": s.tracking_url,
                "shipped_at": s.shipped_at, "delivered_at": s.delivered_at}

    def get_timeline(self, obj) -> list:
        return [{"status": h.to_status, "at": h.created_at} for h in obj.status_history.all()]
