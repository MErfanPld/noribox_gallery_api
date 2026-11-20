from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.exceptions import ConflictError
from core.permissions import AdminModelPermission
from notifications.models import SMSLog, SMSTemplate
from notifications.services import DEFAULT_TEMPLATES, _attempt


class SMSTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSTemplate
        fields = "__all__"

    def validate_body(self, value):
        try:
            value.format_map(_Blank())
        except (ValueError, IndexError, AttributeError, KeyError) as exc:
            raise serializers.ValidationError(f"Invalid template: {exc}") from exc
        return value


class _Blank(dict):
    def __missing__(self, key):
        return ""


class SMSLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSLog
        fields = "__all__"


class AdminSMSTemplateViewSet(viewsets.ModelViewSet):
    queryset = SMSTemplate.objects.all()
    serializer_class = SMSTemplateSerializer
    permission_classes = [AdminModelPermission]

    @extend_schema(responses=dict)
    @action(detail=False)
    def defaults(self, request):
        """Built-in template bodies used when no override exists."""
        return Response(DEFAULT_TEMPLATES)


class AdminSMSLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = SMSLog.objects.all()
    serializer_class = SMSLogSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["status", "template_code", "phone"]
    search_fields = ["phone", "template_code"]
    action_perms = {"retry": ["notifications.retry_sms"]}

    @extend_schema(request=None, responses=SMSLogSerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        log = self.get_object()
        if log.status not in (SMSLog.Status.FAILED, SMSLog.Status.GAVE_UP) or log.message == "[redacted]":
            raise ConflictError("Only failed, non-sensitive messages can be retried.", code="not_retryable")
        log = _attempt(log, SMSTemplate.objects.filter(code=log.template_code).first())  # non-sensitive only
        return Response(SMSLogSerializer(log).data)
