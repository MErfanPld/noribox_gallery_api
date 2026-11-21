from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AuditLog
from core.permissions import AdminModelPermission


class HealthCheckView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(tags=["health"], responses={200: dict, 503: dict})
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False
        return Response(
            {"status": "ok" if db_ok else "degraded", "database": db_ok},
            status=200 if db_ok else 503,
        )


class AuditLogSerializer(serializers.ModelSerializer):
    actor_phone = serializers.CharField(source="actor.phone", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "actor_phone", "action", "target_type", "target_id", "target_repr", "data", "created_at"]


class AdminAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Who did what (approvals, publications, settings changes…). Read-only."""

    queryset = AuditLog.objects.select_related("actor")
    serializer_class = AuditLogSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["action", "target_type", "target_id", "actor"]
    search_fields = ["action", "target_repr"]
