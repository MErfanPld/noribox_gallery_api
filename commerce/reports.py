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


class DashboardView(APIView):
    """Admin dashboard: counts and queues computed live from real records only."""

    permission_classes = [AdminModelPermission]
    required_perms = ["commerce.view_order"]

    @extend_schema(tags=["admin-reports"], parameters=[OpenApiParameter("days", int)], responses=dict)
    def get(self, request):
        from accounts.models import User
        from catalog.models import Product
        from products.models import ImportJob, SourceProduct

        try:
            days = max(1, min(int(request.query_params.get("days", 30)), 365))
        except ValueError:
            days = 30
        since = timezone.now() - timedelta(days=days)
        S = Order.Status
        in_period = Order.objects.filter(created_at__gte=since)
        paid = Order.objects.filter(status__in=PAID, paid_at__gte=since)
        daily = (paid.annotate(day=TruncDate("paid_at")).values("day")
                 .annotate(orders=Count("id"), revenue=Sum("total")).order_by("day"))
        recent_orders = Order.objects.select_related("user").order_by("-created_at")[:8]
        receipts = (PaymentReceipt.objects.filter(status=PaymentReceipt.Status.PENDING)
                    .select_related("payment__order").order_by("created_at")[:8])
        jobs = ImportJob.objects.defer("source_html").order_by("-created_at")[:5]
        products = dict(Product.objects.values_list("status").annotate(c=Count("id")))
        return Response({
            "period_days": days,
            "orders": {
                "total": Order.objects.count(),
                "in_period": in_period.count(),
                "pending": Order.objects.filter(status__in=[S.AWAITING_PAYMENT, S.PAYMENT_SUBMITTED]).count(),
                "awaiting_payment": Order.objects.filter(status=S.AWAITING_PAYMENT).count(),
                "payment_submitted": Order.objects.filter(status=S.PAYMENT_SUBMITTED).count(),
                "processing": Order.objects.filter(status__in=[S.PAID, S.PROCESSING, S.SHIPPED]).count(),
                "completed": Order.objects.filter(status=S.DELIVERED).count(),
                "cancelled": Order.objects.filter(status=S.CANCELLED).count(),
                "needs_review": Order.objects.filter(needs_review=True).count(),
            },
            "sales": {
                "paid_orders": paid.count(),
                "revenue": paid.aggregate(v=Sum("total"))["v"] or 0,
                "daily": [{"day": d["day"], "orders": d["orders"], "revenue": d["revenue"]} for d in daily],
            },
            "customers": {
                "total": User.objects.filter(is_staff=False).count(),
                "new_in_period": User.objects.filter(is_staff=False, date_joined__gte=since).count(),
            },
            "products": {
                "published": products.get("published", 0),
                "draft": products.get("draft", 0),
                "archived": products.get("archived", 0),
            },
            "imports": {
                "pending_review": SourceProduct.objects.filter(review_status=SourceProduct.ReviewStatus.PENDING).count(),
                "running_jobs": ImportJob.objects.filter(status__in=["pending", "running"]).count(),
                "recent_jobs": [{"id": j.id, "status": j.status, "source_url": j.source_url, "stats": j.stats,
                                 "dry_run": j.dry_run, "created_at": j.created_at} for j in jobs],
            },
            "inventory": dict(VariantAvailability.objects.values_list("state").annotate(c=Count("id"))),
            "payment_queue": [{"id": r.id, "order_id": r.payment.order_id, "order_number": r.payment.order.number,
                               "amount": r.payment.amount, "declared_amount": r.declared_amount, "created_at": r.created_at}
                              for r in receipts],
            "payment_queue_total": PaymentReceipt.objects.filter(status=PaymentReceipt.Status.PENDING).count(),
            "recent_orders": [{"id": o.id, "number": o.number, "status": o.status, "total": o.total,
                               "customer": o.user.get_full_name() or o.user.phone, "created_at": o.created_at}
                              for o in recent_orders],
        })
