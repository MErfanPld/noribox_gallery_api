from django.contrib import admin

from payments.models import Payment, PaymentMethod, PaymentReceipt, PaymentReview, StoreBankCard

admin.site.register(PaymentMethod)
admin.site.register(StoreBankCard)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["order", "amount", "status", "created_at"]
    list_filter = ["status"]
    readonly_fields = ["order", "amount", "status", "approved_at"]


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    """Read-only; approve/reject through the admin API so inventory stays consistent."""

    list_display = ["payment", "status", "reservation_status", "created_at"]
    list_filter = ["status", "reservation_status"]
    exclude = ["file"]
    readonly_fields = [f.name for f in PaymentReceipt._meta.fields if f.name != "file"]


@admin.register(PaymentReview)
class PaymentReviewAdmin(admin.ModelAdmin):
    readonly_fields = [f.name for f in PaymentReview._meta.fields]
