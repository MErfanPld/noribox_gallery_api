from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from accounts.models import Address, OTPCode, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    class AddForm(BaseUserCreationForm):
        class Meta:
            model = User
            fields = ("phone",)

    class ChangeForm(UserChangeForm):
        class Meta:
            model = User
            fields = "__all__"

    add_form = AddForm
    form = ChangeForm
    list_filter = ["is_staff", "is_superuser", "is_active", "groups"]
    ordering = ["-date_joined"]
    list_display = ["phone", "first_name", "last_name", "is_staff", "is_active", "date_joined"]
    search_fields = ["phone", "first_name", "last_name", "email"]
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Personal", {"fields": ("first_name", "last_name", "email", "national_code", "phone_verified_at")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "password1", "password2")}),)


admin.site.register(Address)


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display = ["phone", "purpose", "created_at", "expires_at", "attempts", "consumed_at"]
    readonly_fields = [f.name for f in OTPCode._meta.fields]
