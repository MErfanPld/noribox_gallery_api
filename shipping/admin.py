from django.contrib import admin

from shipping.models import Shipment, ShippingMethod

admin.site.register(ShippingMethod)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["order", "status", "tracking_code", "shipped_at"]
    list_filter = ["status"]
