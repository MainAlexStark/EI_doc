from django.contrib import admin
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from apps.catalog.models import (
    AmbientRecord,
    ConditionProfile,
    District,
    MeasurementFamily,
    PricingSettings,
    ProtocolTemplate,
    SiType,
    Standard,
    VerificationMethod,
)


@admin.register(ConditionProfile)
class ConditionProfileAdmin(SimpleHistoryAdmin):
    list_display = ["name", "is_active"]


@admin.register(AmbientRecord)
class AmbientRecordAdmin(admin.ModelAdmin):
    list_display = ["date", "temperature", "pressure", "humidity", "origin"]
    list_filter = ["is_generated"]
    date_hierarchy = "date"

    @admin.display(description="Происхождение")
    def origin(self, obj: AmbientRecord):
        if obj.is_generated:
            return format_html('<span style="color:#9A5F19">сгенерировано</span>')
        return format_html('<span style="color:#2C6B4C">измерено</span>')


@admin.register(MeasurementFamily)
class MeasurementFamilyAdmin(SimpleHistoryAdmin):
    list_display = ["name", "code", "numbering_series", "calculator_key", "price", "requires_time_slot"]
    list_editable = ["price", "requires_time_slot"]
    search_fields = ["name", "code"]


@admin.register(PricingSettings)
class PricingSettingsAdmin(admin.ModelAdmin):
    list_display = ["priority_discount_percent", "is_active"]


@admin.register(SiType)
class SiTypeAdmin(SimpleHistoryAdmin):
    list_display = ["name", "registry_number", "family", "verification_interval_months", "is_active"]
    list_filter = ["family", "is_active"]
    search_fields = ["name", "registry_number", "manufacturer"]


@admin.register(Standard)
class StandardAdmin(SimpleHistoryAdmin):
    list_display = ["name", "fif_number", "valid_until", "validity"]
    list_filter = ["is_active"]
    search_fields = ["name", "fif_number", "serial_number"]

    @admin.display(description="Годность")
    def validity(self, obj: Standard):
        colour = "#2C6B4C" if obj.is_valid else "#9E362E"
        text = "действует" if obj.is_valid else "ИСТЁК"
        return format_html('<b style="color:{}">{}</b>', colour, text)


@admin.register(VerificationMethod)
class VerificationMethodAdmin(admin.ModelAdmin):
    list_display = ["designation", "name"]
    search_fields = ["designation", "name"]
    filter_horizontal = ["families"]


@admin.register(ProtocolTemplate)
class ProtocolTemplateAdmin(SimpleHistoryAdmin):
    list_display = ["family", "version", "valid_from", "is_active", "comment"]
    list_filter = ["family", "is_active"]


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    search_fields = ["name"]

