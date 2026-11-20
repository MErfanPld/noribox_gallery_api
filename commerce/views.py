from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Address
from catalog.models import ProductImage
from commerce import serializers as s
from commerce import services
from commerce.models import Cart, CartItem, Order
from core.exceptions import ServiceError
from core.permissions import AdminModelPermission
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


class AdminOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = s.AdminOrderSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["status", "needs_review", "user"]
    search_fields = ["number", "user__phone", "recipient_name", "recipient_phone"]
    ordering_fields = ["created_at", "total", "paid_at"]
    action_perms = {"transition": ["commerce.manage_order_status"], "clear_review": ["commerce.manage_order_status"],
                    "record_refund": ["commerce.record_refund"]}
    queryset = Order.objects.select_related("user", "shipment").prefetch_related(
        "items", "status_history__changed_by", "payments__receipts__reviews"
    )

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
        return Response(self.get_serializer(order).data)
