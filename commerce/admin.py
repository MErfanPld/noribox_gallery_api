from django.contrib import admin

from commerce.models import Cart, CartItem, Order, OrderItem, OrderStatusHistory


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = [f.name for f in OrderItem._meta.fields]
    can_delete = False


class HistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = [f.name for f in OrderStatusHistory._meta.fields]
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["number", "user", "status", "total", "needs_review", "created_at"]
    list_filter = ["status", "needs_review"]
    search_fields = ["number", "user__phone"]
    readonly_fields = ["number", "user", "status", "subtotal", "shipping_cost", "discount_total", "total", "paid_at", "cancelled_at"]
    inlines = [OrderItemInline, HistoryInline]


admin.site.register(Cart)
admin.site.register(CartItem)
