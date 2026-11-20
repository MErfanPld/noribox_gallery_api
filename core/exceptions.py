"""Consistent API error format and domain exceptions.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "details": {...}}}
"""
import logging

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.db.models import ProtectedError, RestrictedError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("noribox")


class ServiceError(exceptions.APIException):
    """Business-rule violation raised from the service layer."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "business_error"
    default_detail = "Operation not allowed."

    def __init__(self, message=None, code=None, status_code=None, details=None):
        super().__init__(message or self.default_detail, code or self.default_code)
        if status_code:
            self.status_code = status_code
        self.code = code or self.default_code
        self.details = details or {}


class ConflictError(ServiceError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


def protected_conflict(exc, hint="Archive or deactivate it instead."):
    blockers = {}
    for obj in list(getattr(exc, "protected_objects", None) or getattr(exc, "restricted_objects", None) or [])[:50]:
        label = obj._meta.verbose_name_plural.title()
        blockers[label] = blockers.get(label, 0) + 1
    return ConflictError(
        f"This object is referenced by other records and cannot be deleted. {hint}",
        code="protected", details={"referenced_by": blockers},
    )


def api_exception_handler(exc, context):
    if isinstance(exc, (ProtectedError, RestrictedError)):
        exc = protected_conflict(exc)
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(
            exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
        )
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API exception", exc_info=exc)
        return Response(
            {"error": {"code": "server_error", "message": "Internal server error.", "details": {}}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, ServiceError):
        payload = {"code": exc.code, "message": str(exc.detail), "details": exc.details}
    elif isinstance(exc, exceptions.ValidationError):
        payload = {"code": "validation_error", "message": "Invalid input.", "details": response.data}
    else:
        detail = response.data.get("detail", "") if isinstance(response.data, dict) else response.data
        code = getattr(detail, "code", None) or getattr(exc, "default_code", "error")
        payload = {"code": code, "message": str(detail), "details": {}}
        if isinstance(exc, exceptions.Throttled) and exc.wait:
            payload["details"] = {"retry_after_seconds": int(exc.wait)}
    response.data = {"error": payload}
    return response
