from django.contrib import admin, messages
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from apps.verification import numbering
from apps.verification.models import (
    Client,
    ConditionSource,
    Instrument,
    NumberingScope,
    Protocol,
    ProtocolStatus,
    ScanUpload,
    Site,
    Verification,
    WorkOrder,
    WorkOrderStatus,
)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "inn", "phone"]
    list_filter = ["kind"]
    search_fields = ["name", "inn", "phone"]


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ["address", "client", "district", "is_restricted"]
    list_filter = ["is_restricted", "district"]
    search_fields = ["address", "postal_code"]


@admin.register(Instrument)
class InstrumentAdmin(SimpleHistoryAdmin):
    list_display = ["__str__", "serial_number", "manufacture_year", "owner"]
    search_fields = ["serial_number", "si_type__name"]
    list_filter = ["si_type__family"]


@admin.register(Verification)
class VerificationAdmin(SimpleHistoryAdmin):
    list_display = ["instrument", "verifier", "verified_at", "suitable", "status", "conditions"]
    list_filter = ["status", "suitable", "verifier"]
    search_fields = ["instrument__serial_number"]
    date_hierarchy = "verified_at"
    filter_horizontal = ["standards"]

    @admin.display(description="Условия")
    def conditions(self, obj: Verification):
        """Внутренняя пометка: какие условия подставлены, а не измерены.

        В протокол это не выводится — только для нормоконтроля.
        """
        if not obj.has_substituted_conditions:
            return format_html('<span style="color:#2C6B4C">измерены</span>')
        parts = [
            label
            for label, source in (
                ("t", obj.temperature_source),
                ("P", obj.pressure_source),
                ("φ", obj.humidity_source),
            )
            if source != ConditionSource.MEASURED
        ]
        return format_html('<span style="color:#9A5F19">подставлены: {}</span>', ", ".join(parts))


@admin.register(NumberingScope)
class NumberingScopeAdmin(admin.ModelAdmin):
    list_display = ["__str__", "series", "employee", "year", "high_water", "drafts"]
    actions = ["preview_numbering", "apply_numbering"]

    @admin.display(description="Запечатано до")
    def high_water(self, obj: NumberingScope):
        return numbering.sealed_high_water(obj) or "—"

    @admin.display(description="Без номера")
    def drafts(self, obj: NumberingScope):
        return obj.protocols.filter(status=ProtocolStatus.DRAFT).count()

    @admin.action(description="Показать, что даст пересчёт номеров")
    def preview_numbering(self, request, queryset):
        for scope in queryset:
            result = numbering.assign_numbers(scope, dry_run=True)
            if not result.changed:
                self.message_user(request, f"{scope}: менять нечего", messages.INFO)
                continue
            self.message_user(request, f"{scope}: " + "; ".join(result.describe()), messages.WARNING)

    @admin.action(description="Пересчитать номера хвоста")
    def apply_numbering(self, request, queryset):
        for scope in queryset:
            result = numbering.assign_numbers(scope)
            self.message_user(
                request,
                f"{scope}: присвоено {len(result.assigned)}, перенумеровано {len(result.renumbered)}",
                messages.SUCCESS,
            )


@admin.register(Protocol)
class ProtocolAdmin(SimpleHistoryAdmin):
    list_display = ["full_number", "verified_at", "status", "signed_by", "fif_record_number"]
    list_filter = ["status", "scope"]
    search_fields = ["verification__instrument__serial_number"]
    readonly_fields = ["seq", "suffix", "sha256", "numbered_at", "signed_at", "published_at"]

    @admin.display(description="Номер", ordering="seq")
    def full_number(self, obj: Protocol):
        return obj.full_number or "—"

    @admin.display(description="Дата поверки", ordering="verification__verified_at")
    def verified_at(self, obj: Protocol):
        return obj.verification.verified_at

@admin.register(ScanUpload)
class ScanUploadAdmin(admin.ModelAdmin):
    list_display = ["id", "work_order", "verification", "uploaded_by", "created_at", "has_error"]
    list_filter = ["uploaded_by"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["work_order", "verification"]

    @admin.display(description="Ошибка", boolean=True)
    def has_error(self, obj: ScanUpload) -> bool:
        return bool(obj.error)


@admin.register(WorkOrder)
class WorkOrderAdmin(SimpleHistoryAdmin):
    list_display = [
        "id", "site", "client", "assigned_employee", "status_badge", "scheduled_date", "request",
    ]
    list_filter = ["status", "assigned_employee"]
    search_fields = ["site__address", "client__name"]
    date_hierarchy = "scheduled_date"
    readonly_fields = ["created_at", "closed_at"]

    @admin.display(description="Статус")
    def status_badge(self, obj: WorkOrder):
        colours = {
            WorkOrderStatus.PLANNED: "#6E7A7E",
            WorkOrderStatus.IN_PROGRESS: "#0E6E75",
            WorkOrderStatus.DONE: "#2C6B4C",
            WorkOrderStatus.CANCELLED: "#9E362E",
        }
        return format_html(
            '<b style="color:{}">{}</b>', colours.get(obj.status, "#000"), obj.get_status_display()
        )
