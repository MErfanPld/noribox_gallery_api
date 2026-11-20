from django.contrib import admin

from inventory.models import Reservation, VariantAvailability


@admin.register(VariantAvailability)
class VariantAvailabilityAdmin(admin.ModelAdmin):
    list_display = ["variant", "state", "sold", "updated_at"]
    list_filter = ["state", "sold"]
    search_fields = ["variant__sku"]
    # State is changed through inventory services (admin API) to keep reservations consistent.
    readonly_fields = ["variant", "state", "sold", "updated_by", "updated_at"]


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ["variant", "order", "status", "expires_at", "created_at"]
    list_filter = ["status"]
    readonly_fields = [f.name for f in Reservation._meta.fields]
