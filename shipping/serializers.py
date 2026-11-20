from rest_framework import serializers

from shipping.models import Shipment, ShippingMethod


class ShippingMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingMethod
        fields = ["id", "name", "code", "description", "cost", "free_shipping_threshold",
                  "estimated_days_min", "estimated_days_max", "supports_tracking"]


class AdminShippingMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingMethod
        fields = "__all__"


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["status", "method_name", "carrier", "tracking_code", "tracking_url", "shipped_at", "delivered_at"]


class AdminShipmentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True)

    class Meta:
        model = Shipment
        fields = ["id", "order", "order_number", "method", "method_name", "status", "carrier", "tracking_code",
                  "tracking_url", "shipped_at", "delivered_at", "note", "created_at", "updated_at"]
        read_only_fields = ["order", "method", "method_name", "status", "shipped_at", "delivered_at", "created_at", "updated_at"]

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        method = instance.method
        if instance.tracking_code and not instance.tracking_url and method and method.tracking_url_template:
            instance.tracking_url = method.tracking_url_template.replace("{code}", instance.tracking_code)
            instance.save(update_fields=["tracking_url"])
        return instance
