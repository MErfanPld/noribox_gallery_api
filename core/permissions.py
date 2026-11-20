"""Role-based access control for the management API.

Roles are Django Groups (see ``manage.py setup_roles``); every admin endpoint
requires ``is_staff`` plus the Django model permission matching the HTTP method.
Custom actions declare ``required_perms`` on the view/action.
"""
from rest_framework.permissions import BasePermission

METHOD_PERMS = {
    "GET": "view",
    "HEAD": "view",
    "OPTIONS": "view",
    "POST": "add",
    "PUT": "change",
    "PATCH": "change",
    "DELETE": "delete",
}


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_staff)


class AdminModelPermission(IsStaff):
    """Staff + model permission. Actions may override via ``required_perms``."""

    def get_required_perms(self, request, view):
        action = getattr(view, "action", None)
        action_perms = getattr(view, "action_perms", {}) or {}
        if action and action in action_perms:
            return action_perms[action]
        explicit = getattr(view, "required_perms", None)
        if explicit is not None:
            return explicit
        queryset = getattr(view, "queryset", None)
        if queryset is None and hasattr(view, "get_queryset"):
            queryset = view.get_queryset()
        model = queryset.model
        verb = METHOD_PERMS.get(request.method, "change")
        return [f"{model._meta.app_label}.{verb}_{model._meta.model_name}"]

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.user.is_superuser:
            return True
        return request.user.has_perms(self.get_required_perms(request, view))
