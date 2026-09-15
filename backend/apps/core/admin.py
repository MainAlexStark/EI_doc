from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from simple_history.admin import SimpleHistoryAdmin

from apps.core.models import Attestation, Device, Employee, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "role", "is_active", "is_staff"]
    list_filter = ["role", "is_active", "is_staff"]
    search_fields = ["email"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Личное", {"fields": ("first_name", "last_name")}),
        ("Доступ", {"fields": ("role", "is_active", "is_staff", "is_superuser", "groups")}),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "role")}),
    )


class AttestationInline(admin.TabularInline):
    model = Attestation
    extra = 0
    filter_horizontal = ["families"]


@admin.register(Employee)
class EmployeeAdmin(SimpleHistoryAdmin):
    list_display = ["full_name", "tab_number", "position", "telegram_chat_id", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["full_name", "tab_number"]
    inlines = [AttestationInline]
    # Chat_id заполняется только через привязку кодом (сотрудник сам, из своего
    # профиля в EI_doc) — руками в админке при создании учётки его проставлять
    # не должно быть можно, иначе легко привязать чужой чат по ошибке.
    readonly_fields = ["telegram_chat_id"]


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ["__str__", "employee", "last_seen_at", "is_revoked"]
    list_filter = ["is_revoked"]
    actions = ["revoke"]

    @admin.action(description="Отозвать доступ устройству")
    def revoke(self, request, queryset):
        updated = queryset.update(is_revoked=True)
        self.message_user(request, f"Отозвано устройств: {updated}")
