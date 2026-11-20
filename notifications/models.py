from django.db import models

from core.models import TimeStampedModel


class SMSTemplate(TimeStampedModel):
    code = models.SlugField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(help_text="Python format placeholders, e.g. {order_number}")
    is_active = models.BooleanField(default=True, help_text="Disable to stop sending this event")
    provider_template = models.CharField(
        max_length=100, blank=True, default="", help_text="Optional provider-side template id (e.g. Kavenegar verify)"
    )

    def __str__(self):
        return self.code


class SMSLog(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed (will retry)"
        GAVE_UP = "gave_up", "Failed permanently"
        SKIPPED = "skipped", "Skipped (disabled)"

    phone = models.CharField(max_length=11, db_index=True)
    template_code = models.CharField(max_length=64, db_index=True)
    message = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    provider = models.CharField(max_length=50, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    provider_message_id = models.CharField(max_length=128, blank=True, default="")
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("retry_sms", "Can retry failed SMS")]
