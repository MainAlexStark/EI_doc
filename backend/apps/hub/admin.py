from django.contrib import admin
from django.utils.html import format_html

from apps.hub.models import DistrictAssignment, EmployeeAvailability, Request, RequestItem, RequestStatus


class DistrictAssignmentInline(admin.TabularInline):
    model = DistrictAssignment
    extra = 0


@admin.register(DistrictAssignment)
class DistrictAssignmentAdmin(admin.ModelAdmin):
    list_display = ["district", "employee", "priority", "is_active"]
    list_filter = ["district", "is_active"]
    ordering = ["district", "priority"]


@admin.register(EmployeeAvailability)
class EmployeeAvailabilityAdmin(admin.ModelAdmin):
    """Сотрудники ведут свой календарь сами через SPA («Мой график») —

    админка нужна только руководителю, чтобы посмотреть или поправить чужой слот.
    """

    list_display = ["employee", "date", "start_time", "end_time", "kind", "is_priority"]
    list_filter = ["kind", "is_priority", "employee"]
    date_hierarchy = "date"


class RequestItemInline(admin.TabularInline):
    model = RequestItem
    extra = 0


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = [
        "id", "contact_name", "address", "district", "status_badge",
        "suggested_employee", "assigned_employee", "desired_date", "desired_time",
        "estimated_price", "created_at",
    ]
    list_filter = ["status", "source", "district", "is_priority_slot"]
    search_fields = ["contact_name", "contact_phone", "contact_email", "address"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "routed_at", "confirmed_at", "ip_address", "honeypot_tripped"]
    inlines = [RequestItemInline]

    @admin.display(description="Статус")
    def status_badge(self, obj: Request):
        colours = {
            RequestStatus.NEW: "#6E7A7E",
            RequestStatus.ROUTED: "#0E6E75",
            RequestStatus.CONFIRMED: "#2C6B4C",
            RequestStatus.REJECTED: "#9E362E",
            RequestStatus.SPAM: "#9E362E",
        }
        return format_html(
            '<b style="color:{}">{}</b>', colours.get(obj.status, "#000"), obj.get_status_display()
        )
