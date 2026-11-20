from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.permissions import AdminModelPermission
from inventory.models import AvailabilityState, Reservation, VariantAvailability
from inventory.services import release_for_order, set_availability


class AvailabilitySerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="variant.sku", read_only=True)
    product_name = serializers.CharField(source="variant.product.name", read_only=True)
    active_reservation_order = serializers.SerializerMethodField()

    class Meta:
        model = VariantAvailability
        fields = ["id", "variant", "sku", "product_name", "state", "sold", "note", "updated_by", "updated_at", "active_reservation_order"]
        read_only_fields = fields

    def get_active_reservation_order(self, obj) -> str | None:
        r = next((r for r in obj.variant.reservations.all() if r.status == "active"), None)
        return r.order.number if r else None


class SetAvailabilitySerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=[AvailabilityState.AVAILABLE, AvailabilityState.UNAVAILABLE])
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class ReservationSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True)
    sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = Reservation
        fields = ["id", "variant", "sku", "order", "order_number", "status", "expires_at", "closed_at", "close_reason", "created_at"]
        read_only_fields = fields


class AdminAvailabilityViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = VariantAvailability.objects.select_related("variant__product").prefetch_related("variant__reservations__order")
    serializer_class = AvailabilitySerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["state", "sold", "variant__product"]
    search_fields = ["variant__sku", "variant__product__name"]
    action_perms = {"set_state": ["inventory.manage_inventory"]}

    @extend_schema(request=SetAvailabilitySerializer, responses=AvailabilitySerializer)
    @action(detail=True, methods=["post"], url_path="set-state")
    def set_state(self, request, pk=None):
        obj = self.get_object()
        ser = SetAvailabilitySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        row = set_availability(obj.variant, ser.validated_data["state"], request.user, ser.validated_data.get("note", ""))
        return Response(AvailabilitySerializer(self.get_queryset().get(pk=row.pk)).data)


class AdminReservationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Reservation.objects.select_related("order", "variant")
    serializer_class = ReservationSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["status", "order", "variant"]
    search_fields = ["order__number", "variant__sku"]
    action_perms = {"release": ["inventory.manage_inventory"]}

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        reservation = self.get_object()
        count = release_for_order(reservation.order, reason=f"released by admin {request.user.phone}")
        reservation.order.flag_for_review("Reservation released manually by an administrator.")
        return Response({"released": count})
