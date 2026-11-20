from django.contrib import admin

from notifications.models import SMSLog, SMSTemplate

admin.site.register(SMSTemplate)


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ["phone", "template_code", "status", "attempts", "created_at"]
    list_filter = ["status", "template_code"]
    readonly_fields = [f.name for f in SMSLog._meta.fields]
