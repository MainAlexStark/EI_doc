from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from apps.equipment.models import (
    AttestationCounter,
    AttestationProgram,
    EquipmentAttestation,
)


@admin.register(AttestationProgram)
class AttestationProgramAdmin(SimpleHistoryAdmin):
    list_display = ("title", "equipment_kind", "needs_review", "is_active")
    list_filter = ("needs_review", "is_active")
    search_fields = ("title", "equipment_kind")


@admin.register(AttestationCounter)
class AttestationCounterAdmin(admin.ModelAdmin):
    list_display = ("year", "last")


@admin.register(EquipmentAttestation)
class EquipmentAttestationAdmin(SimpleHistoryAdmin):
    list_display = ("__str__", "status", "suitable", "performed_on", "protocol_date", "created_via_assistant")
    list_filter = ("status", "kind", "created_via_assistant")
    readonly_fields = ("number", "year", "results", "snapshot", "sha256", "signed_by", "signed_at")
