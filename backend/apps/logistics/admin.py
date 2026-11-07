from django.contrib import admin

from apps.logistics.models import StandardReservation, Trip, TripStop


class TripStopInline(admin.TabularInline):
    model = TripStop
    extra = 0
    raw_id_fields = ["work_order"]
    fields = ["day", "position", "address", "eta", "window_from", "window_to", "status", "work_order"]


class ReservationInline(admin.TabularInline):
    model = StandardReservation
    extra = 0


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["title", "date_from", "date_to", "status", "district"]
    list_filter = ["status", "district"]
    filter_horizontal = ["employees"]
    inlines = [TripStopInline, ReservationInline]


@admin.register(StandardReservation)
class StandardReservationAdmin(admin.ModelAdmin):
    list_display = ["standard", "trip", "date_from", "date_to"]
