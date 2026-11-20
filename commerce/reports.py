from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from commerce.models import Order
from core.permissions import AdminModelPermission
from inventory.models import VariantAvailability
from payments.models import PaymentReceipt

PAID = [Order.Status.PAID, Order.Status.PROCESSING, Order.Status.SHIPPED, Order.Status.DELIVERED]


class ReportSummaryView(APIView):
    permission_classes = [AdminModelPermission]
    required_perms = ["commerce.view_order"]

    @extend_schema(tags=["admin-reports"], parameters=[OpenApiParameter("days", int)], responses=dict)
    def get(self, request):
        try:
            days = max(1, min(int(request.query_params.get("days", 30)), 365))
        except ValueError:
            days = 30
        since = timezone.now() - timedelta(days=days)
        paid = Order.objects.filter(status__in=PAID, paid_at__gte=since)
        daily = (
            paid.annotate(day=TruncDate("paid_at")).values("day")
            .annotate(orders=Count("id"), revenue=Sum("total")).order_by("day")
        )
        return Response({
            "period_days": days,
            "paid_orders": paid.count(),
            "revenue": paid.aggregate(v=Sum("total"))["v"] or 0,
            "orders_by_status": dict(Order.objects.values_list("status").annotate(c=Count("id"))),
            "orders_needing_review": Order.objects.filter(needs_review=True).count(),
            "receipts_pending_review": PaymentReceipt.objects.filter(status=PaymentReceipt.Status.PENDING).count(),
            "inventory": dict(VariantAvailability.objects.values_list("state").annotate(c=Count("id"))),
            "daily": [{"day": d["day"], "orders": d["orders"], "revenue": d["revenue"]} for d in daily],
        })
