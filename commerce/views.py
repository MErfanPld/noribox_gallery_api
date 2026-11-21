from django.conf import settings
import django_filters
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Address
from catalog.models import ProductImage
from commerce import serializers as s
from commerce import services
from commerce.models import Cart, CartItem, Order
from core import audit, ratelimit
from core.exceptions import ServiceError
from core.permissions import AdminModelPermission
from core.throttling import ScopedRateThrottle
from shipping.models import ShippingMethod


def cart_queryset():
    return Cart.objects.prefetch_related(
        Prefetch(
            "items",
            queryset=CartItem.objects.select_related("variant__product", "variant__availability").prefetch_related(
                "variant__options__attribute",
                Prefetch("variant__product__images", queryset=ProductImage.objects.order_by("-is_primary", "sort_order")),
            ),
        )
    )


class CartView(APIView):
    @extend_schema(tags=["cart"], responses=s.CartSerializer)
    def get(self, request):
        cart = services.get_cart(request.user)
        return Response(s.CartSerializer(cart_queryset().get(pk=cart.pk), context={"request": request}).data)

    @extend_schema(tags=["cart"], responses={204: None})
    def delete(self, request):
        services.get_cart(request.user).items.all().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CartItemView(APIView):
    @extend_schema(tags=["cart"], request=s.AddCartItemSerializer, responses={201: s.CartSerializer, 200: s.CartSerializer})
    def post(self, request):
        ser = s.AddCartItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        item, created = services.add_to_cart(request.user, ser.validated_data["variant_id"])
        cart = cart_queryset().get(pk=item.cart_id)
        return Response(s.CartSerializer(cart, context={"request": request}).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CartItemDeleteView(APIView):
    @extend_schema(tags=["cart"], responses={204: None})
    def delete(self, request, item_id):
        item = get_object_or_404(CartItem, pk=item_id, cart__user=request.user)
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    lookup_field = "number"
    filterset_fields = ["status"]
    ordering_fields = ["created_at", "total"]
    search_fields = []

    def get_queryset(self):
        # Ownership scoping prevents IDOR: other users' orders are 404.
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        return Order.objects.filter(user=self.request.user).prefetch_related(
            "items", "status_history", "payments__receipts__reviews"
        ).select_related("shipment")

    def get_serializer_class(self):
        if self.action == "list":
            return s.OrderListSerializer
        if self.action == "create":
            return s.CreateOrderSerializer
        return s.OrderDetailSerializer

    @extend_schema(request=s.CreateOrderSerializer, responses={201: s.OrderDetailSerializer})
    def create(self, request):
        ser = s.CreateOrderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        address = Address.objects.filter(pk=ser.validated_data["address_id"], user=request.user).first()
        if address is None:
            raise ServiceError("Address not found.", code="address_not_found", status_code=404)
        method = ShippingMethod.objects.filter(pk=ser.validated_data["shipping_method_id"], is_active=True).first()
        if method is None:
            raise ServiceError("Shipping method not found.", code="shipping_unavailable", status_code=404)
        order = services.create_order(request.user, address, method, ser.validated_data.get("customer_note", ""))
        return Response(s.OrderDetailSerializer(self.get_queryset().get(pk=order.pk), context={"request": request}).data,
                        status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=s.OrderDetailSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, number=None):
        order = self.get_object()
        services.cancel_order(order, request.user, "Cancelled by customer")
        return Response(s.OrderDetailSerializer(self.get_queryset().get(pk=order.pk), context={"request": request}).data)


OrderViewSet.http_method_names = ["get", "post", "head", "options"]


class TrackOrderView(APIView):
    """Guest order tracking by order number + phone (account owner's or the order's recipient).

    Unknown orders and wrong phones get the same 404. Throttled per client IP
    and locked per order number after repeated failures.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = "order_track"

    def get_throttles(self):
        return [ScopedRateThrottle(), *super().get_throttles()]

    @extend_schema(tags=["orders"], request=s.TrackOrderRequestSerializer, responses={200: s.TrackedOrderSerializer})
    def post(self, request):
        ser = s.TrackOrderRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        number, phone = ser.validated_data["number"], ser.validated_data["phone"]
        if ratelimit.is_locked("order_track", number, settings.ORDER_TRACK_MAX_FAILURES):
            raise ServiceError("Too many attempts. Try again later.", code="track_locked",
                               status_code=status.HTTP_429_TOO_MANY_REQUESTS)
        order = (
            Order.objects.select_related("user", "shipment")
            .prefetch_related("items", "status_history", "payments__receipts")
            .filter(number=number).first()
        )
        if order is None or not _phone_matches(order, phone):
            ratelimit.register_failure("order_track", number, settings.ORDER_TRACK_FAILURE_WINDOW_SECONDS)
            raise ServiceError("Order not found.", code="order_not_found", status_code=status.HTTP_404_NOT_FOUND)
        ratelimit.reset("order_track", number)
        return Response(s.TrackedOrderSerializer(order, context={"request": request}).data)


def _phone_matches(order, phone):
    import hmac

    from core.utils import normalize_phone

    candidates = [order.user.phone, order.recipient_phone]
    matched = False
    for candidate in candidates:
        try:
            normalized = normalize_phone(candidate)
        except ValueError:
            continue
        matched |= hmac.compare_digest(normalized, phone)  # evaluate all candidates (no early exit)
    return matched


class AdminOrderFilter(django_filters.FilterSet):
    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")
    status__in = django_filters.BaseInFilter(field_name="status")
    payment_status = django_filters.CharFilter(field_name="payments__status")

    class Meta:
        model = Order
        fields = ["status", "needs_review", "user", "refund_status"]


class AdminOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = s.AdminOrderSerializer
    permission_classes = [AdminModelPermission]
    filterset_class = AdminOrderFilter
    search_fields = ["number", "user__phone", "user__first_name", "user__last_name", "recipient_name", "recipient_phone"]
    ordering_fields = ["created_at", "total", "paid_at"]
    action_perms = {"transition": ["commerce.manage_order_status"], "clear_review": ["commerce.manage_order_status"],
                    "record_refund": ["commerce.record_refund"], "notes": ["commerce.view_order"]}
    queryset = Order.objects.select_related("user", "shipment").prefetch_related(
        "items", "status_history__changed_by", "payments__receipts__reviews", "notes__author"
    )

    def get_queryset(self):
        if self.action == "list":
            return Order.objects.select_related("user").prefetch_related("payments").annotate(
                item_count=Count("items", distinct=True)).distinct().order_by("-created_at", "-id")
        return self.queryset

    def get_serializer_class(self):
        return s.AdminOrderListSerializer if self.action == "list" else s.AdminOrderSerializer

    def perform_update(self, serializer):
        serializer.save()
        audit.record(self.request.user, "order.update_note", serializer.instance)

    @extend_schema(request=s.OrderNoteSerializer, responses=s.OrderNoteSerializer(many=True))
    @action(detail=True, methods=["get", "post"])
    def notes(self, request, pk=None):
        """Internal notes (staff only). Adding one needs change_order."""
        order = self.get_object()
        if request.method == "POST":
            if not (request.user.is_superuser or request.user.has_perm("commerce.change_order")):
                raise PermissionDenied()
            ser = s.OrderNoteSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            ser.save(order=order, author=request.user)
            audit.record(request.user, "order.note", order)
            return Response(ser.data, status=status.HTTP_201_CREATED)
        return Response(s.OrderNoteSerializer(order.notes.select_related("author"), many=True).data)

    @extend_schema(request=s.AdminTransitionSerializer, responses=s.AdminOrderSerializer)
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        order = self.get_object()
        ser = s.AdminTransitionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.admin_transition(order, ser.validated_data["status"], request.user, ser.validated_data.get("note", ""))
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)

    @extend_schema(request=s.RecordRefundSerializer, responses=s.AdminOrderSerializer)
    @action(detail=True, methods=["post"], url_path="record-refund")
    def record_refund(self, request, pk=None):
        """Record a refund that was made manually (bank transfer) for a cancelled paid order."""
        ser = s.RecordRefundSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.record_refund(self.get_object(), request.user, ser.validated_data["reference"], ser.validated_data.get("note", ""))
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)

    @extend_schema(request=None, responses=s.AdminOrderSerializer)
    @action(detail=True, methods=["post"], url_path="clear-review")
    def clear_review(self, request, pk=None):
        order = self.get_object()
        order.needs_review, order.review_reason = False, ""
        order.save(update_fields=["needs_review", "review_reason", "updated_at"])
        audit.record(request.user, "order.clear_review", order)
        return Response(self.get_serializer(order).data)
