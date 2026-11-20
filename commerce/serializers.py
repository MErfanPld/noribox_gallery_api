from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from catalog.serializers import PublicVariantSerializer
from commerce.models import Cart, CartItem, Order, OrderItem, OrderStatusHistory
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
    history = OrderStatusHistorySerializer(source="status_history", many=True, read_only=True)
    shipment = ShipmentSerializer(read_only=True)
    payment = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["number", "status", *ADDRESS_FIELDS, "shipping_method_name", "subtotal", "shipping_cost",
                  "discount_total", "total", "customer_note", "items", "shipment", "payment", "history",
                  "created_at", "paid_at", "cancelled_at"]

    @extend_schema_field(PaymentSerializer(allow_null=True))
    def get_payment(self, obj):
        from payments.services import current_payment

        payment = current_payment(obj)
        return PaymentSerializer(payment, context=self.context).data if payment else None


class CreateOrderSerializer(serializers.Serializer):
    address_id = serializers.IntegerField()
    shipping_method_id = serializers.IntegerField()
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class AdminOrderSerializer(OrderDetailSerializer):
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    history = serializers.SerializerMethodField()

    class Meta(OrderDetailSerializer.Meta):
        fields = ["id", "user", "user_phone", *OrderDetailSerializer.Meta.fields, "admin_note", "needs_review", "review_reason", "updated_at"]
        read_only_fields = [f for f in fields if f not in ("admin_note",)]

    def get_history(self, obj) -> list:
        return [
            {"from_status": h.from_status, "to_status": h.to_status, "note": h.note, "created_at": h.created_at,
             "changed_by": h.changed_by.phone if h.changed_by else None}
            for h in obj.status_history.all()
        ]


class AdminTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)
