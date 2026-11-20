from rest_framework import mixins, permissions, viewsets

from core.permissions import AdminModelPermission
from shipping import serializers as s
from shipping.models import Shipment, ShippingMethod


class ShippingMethodViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ShippingMethod.objects.filter(is_active=True)
    serializer_class = s.ShippingMethodSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    filter_backends = []


class AdminShippingMethodViewSet(viewsets.ModelViewSet):
    queryset = ShippingMethod.objects.all()
    serializer_class = s.AdminShippingMethodSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["is_active"]


class AdminShipmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Tracking info is edited here; status changes go through order transitions."""

    queryset = Shipment.objects.select_related("order", "method")
    serializer_class = s.AdminShipmentSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["status", "method"]
    search_fields = ["order__number", "tracking_code"]
