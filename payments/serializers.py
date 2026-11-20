from django.urls import reverse
from rest_framework import serializers

from payments.models import Payment, PaymentMethod, PaymentReceipt, PaymentReview, StoreBankCard


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ["id", "code", "name", "kind", "instructions"]


class StoreBankCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreBankCard
        fields = ["id", "bank_name", "holder_name", "card_number", "sheba", "account_number"]


class AdminPaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = "__all__"


class AdminStoreBankCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreBankCard
        fields = "__all__"

    def validate_card_number(self, value):
        value = value.replace("-", "").replace(" ", "")
        if not value.isdigit() or len(value) != 16:
            raise serializers.ValidationError("Card number must be 16 digits.")
        return value

    def validate_sheba(self, value):
        value = value.replace(" ", "").upper()
        if value and (not value.startswith("IR") or len(value) != 26 or not value[2:].isdigit()):
            raise serializers.ValidationError("SHEBA must be IR followed by 24 digits.")
        return value


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentReview
        fields = ["decision", "reason", "created_at"]


class ReceiptSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    reviews = ReviewSerializer(many=True, read_only=True)

    class Meta:
        model = PaymentReceipt
        fields = ["id", "status", "original_filename", "content_type", "size", "reference_number", "payer_card_last4",
                  "paid_at", "declared_amount", "customer_note", "file_url", "reviews", "created_at"]

    def get_file_url(self, obj) -> str:
        # Authenticated, ownership-checked download endpoint (never a public media URL).
        return reverse("receipt-file", kwargs={"pk": obj.pk})


class PaymentSerializer(serializers.ModelSerializer):
    method = PaymentMethodSerializer(read_only=True)
    receipts = ReceiptSerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "method", "amount", "status", "approved_at", "receipts", "created_at"]


class UploadReceiptSerializer(serializers.Serializer):
    file = serializers.FileField()
    reference_number = serializers.CharField(required=False, allow_blank=True, max_length=100)
    payer_card_last4 = serializers.RegexField(r"^\d{4}$", required=False, allow_blank=True)
    paid_at = serializers.DateTimeField(required=False, allow_null=True)
    declared_amount = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    customer_note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class AdminReceiptSerializer(ReceiptSerializer):
    order_number = serializers.CharField(source="payment.order.number", read_only=True)
    order_id = serializers.IntegerField(source="payment.order_id", read_only=True)
    order_total = serializers.IntegerField(source="payment.amount", read_only=True)
    uploaded_by_phone = serializers.CharField(source="uploaded_by.phone", read_only=True)
    reviews = serializers.SerializerMethodField()

    class Meta(ReceiptSerializer.Meta):
        fields = ReceiptSerializer.Meta.fields + ["order_number", "order_id", "order_total", "uploaded_by_phone",
                                                  "sha256", "reservation_status", "reservation_detail"]

    def get_file_url(self, obj) -> str:
        return reverse("admin-receipt-file", kwargs={"pk": obj.pk})

    def get_reviews(self, obj) -> list:
        return [{"decision": r.decision, "reason": r.reason, "reviewer": r.reviewer.phone, "created_at": r.created_at}
                for r in obj.reviews.all()]


class AdminPaymentSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True)
    receipts = AdminReceiptSerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "order", "order_number", "method", "amount", "status", "approved_at", "receipts", "created_at", "updated_at"]


class ReviewActionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
