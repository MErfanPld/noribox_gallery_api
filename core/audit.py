"""Helper for writing AuditLog entries from services and admin views."""
from core.models import AuditLog


def record(actor, action, target, data=None, target_type=None):
    """Record ``action`` on ``target`` (a model instance) by ``actor``. Never raises on bad actor."""
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    return AuditLog.objects.create(
        actor=actor,
        action=action[:64],
        target_type=target_type or f"{target._meta.app_label}.{target._meta.model_name}",
        target_id=str(target.pk),
        target_repr=str(target)[:255],
        data=data or {},
    )
