from django.http import FileResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from core.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from commerce.models import Order
from core.permissions import AdminModelPermission
from payments import serializers as s
from payments import services
from payments.models import Payment, PaymentMethod, PaymentReceipt, StoreBankCard


def _file_response(receipt):
    f = receipt.file.open("rb")
    resp = FileResponse(f, content_type=receipt.content_type, as_attachment=False, filename=f"receipt-{receipt.pk}.{receipt.file.name.rsplit('.', 1)[-1]}")
    resp["Cache-Control"] = "private, no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
    return resp


class PaymentMethodListView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["payments"], responses=s.PaymentMethodSerializer(many=True))
    def get(self, request):
        return Response(s.PaymentMethodSerializer(PaymentMethod.objects.filter(is_active=True), many=True).data)


class BankCardListView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["payments"], responses=s.StoreBankCardSerializer(many=True))
    def get(self, request):
        return Response(s.StoreBankCardSerializer(StoreBankCard.objects.filter(is_active=True), many=True).data)


class OrderReceiptUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = "receipt_upload"

    def get_throttles(self):
        return [ScopedRateThrottle(), *super().get_throttles()]

    @extend_schema(tags=["payments"], request=s.UploadReceiptSerializer, responses={201: s.ReceiptSerializer})
    def post(self, request, number):
        order = get_object_or_404(Order, number=number, user=request.user)
        ser = s.UploadReceiptSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        receipt = services.upload_receipt(
            order, request.user, data["file"],
            reference_number=data.get("reference_number", ""),
            payer_card_last4=data.get("payer_card_last4", ""),
            paid_at=data.get("paid_at"),
            declared_amount=data.get("declared_amount"),
            customer_note=data.get("customer_note", ""),
        )
        return Response(s.ReceiptSerializer(receipt).data, status=status.HTTP_201_CREATED)


class ReceiptFileView(APIView):
    @extend_schema(tags=["payments"], responses={(200, "application/octet-stream"): bytes})
    def get(self, request, pk):
        receipt = get_object_or_404(PaymentReceipt, pk=pk, payment__order__user=request.user)
        return _file_response(receipt)


# --- admin -------------------------------------------------------------------
class AdminPaymentMethodViewSet(viewsets.ModelViewSet):
    queryset = PaymentMethod.objects.all()
    serializer_class = s.AdminPaymentMethodSerializer
    permission_classes = [AdminModelPermission]


class AdminBankCardViewSet(viewsets.ModelViewSet):
    queryset = StoreBankCard.objects.all()
    serializer_class = s.AdminStoreBankCardSerializer
    permission_classes = [AdminModelPermission]


class AdminPaymentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Payment.objects.select_related("order", "method").prefetch_related("receipts__reviews__reviewer", "receipts__uploaded_by")
    serializer_class = s.AdminPaymentSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["status", "order"]
    search_fields = ["order__number"]


class AdminReceiptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = PaymentReceipt.objects.select_related("payment__order", "uploaded_by").prefetch_related("reviews__reviewer")
    serializer_class = s.AdminReceiptSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["status", "reservation_status", "payment__order"]
    search_fields = ["payment__order__number", "reference_number", "uploaded_by__phone"]
    action_perms = {"approve": ["payments.review_payment"], "reject": ["payments.review_payment"],
                    "retry_reservation": ["payments.review_payment"]}

    @extend_schema(request=None, responses=s.AdminReceiptSerializer)
    @action(detail=True, methods=["post"], url_path="retry-reservation")
    def retry_reservation(self, request, pk=None):
        """Re-attempt the atomic reservation for a pending receipt (e.g. after resolving a conflict)."""
        receipt = services.attempt_reservation(self.get_object())
        return Response(self.get_serializer(self.get_queryset().get(pk=receipt.pk)).data)

    @extend_schema(request=s.ReviewActionSerializer, responses=s.AdminReceiptSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        ser = s.ReviewActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        receipt = services.approve_receipt(self.get_object(), request.user, ser.validated_data.get("reason", ""))
        return Response(self.get_serializer(self.get_queryset().get(pk=receipt.pk)).data)

    @extend_schema(request=s.ReviewActionSerializer, responses=s.AdminReceiptSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        ser = s.ReviewActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        receipt = services.reject_receipt(self.get_object(), request.user, ser.validated_data.get("reason", ""))
        return Response(self.get_serializer(self.get_queryset().get(pk=receipt.pk)).data)


class AdminReceiptFileView(APIView):
    permission_classes = [AdminModelPermission]
    queryset = PaymentReceipt.objects.all()

    @extend_schema(tags=["admin-payments"], responses={(200, "application/octet-stream"): bytes})
    def get(self, request, pk):
        return _file_response(get_object_or_404(PaymentReceipt, pk=pk))
