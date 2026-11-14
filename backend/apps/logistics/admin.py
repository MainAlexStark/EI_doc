from django.contrib import admin

from apps.logistics.models import (
    Pass,
    Procurement,
    ProcurementDocument,
    ProcurementItem,
    Trip,
    TripStop,
)


class ProcurementItemInline(admin.TabularInline):
    model = ProcurementItem
    extra = 0


class ProcurementDocumentInline(admin.TabularInline):
    model = ProcurementDocument
    extra = 0


class PassInline(admin.TabularInline):
    model = Pass
    extra = 0
    autocomplete_fields = ["site"]


@admin.register(Procurement)
class ProcurementAdmin(admin.ModelAdmin):
    list_display = ["__str__", "customer", "law", "contract_number", "deadline", "status"]
    list_filter = ["status", "law"]
    search_fields = ["number", "contract_number", "title", "customer__name"]
    inlines = [ProcurementItemInline, ProcurementDocumentInline, PassInline]


class TripStopInline(admin.TabularInline):
    model = TripStop
    extra = 0
    fields = ["position", "site", "client", "status", "planned_count", "note", "work_order"]
    raw_id_fields = ["site", "client", "work_order"]


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "destination", "date_start", "date_end", "lead", "status"]
    list_filter = ["kind", "status"]
    search_fields = ["title", "destination", "procurement__number"]
    filter_horizontal = ["members"]
    inlines = [TripStopInline]
