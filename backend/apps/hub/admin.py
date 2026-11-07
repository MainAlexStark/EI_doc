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
    readonly_fields = ["created_at", "routed_at", "confirmed_at", "ip_address", "honeypot_tripped",
                       "consent_given", "consent_at", "consent_policy_version", "anonymized_at"]
    inlines = [RequestItemInline]
    actions = ["anonymize_selected"]

    @admin.action(description="Обезличить персональные данные (запрос субъекта, 152-ФЗ)")
    def anonymize_selected(self, request, queryset):
        from apps.core.models import PersonalDataEvent
        from apps.core.personal_data import anonymize_request

        done = sum(anonymize_request(obj, action=PersonalDataEvent.Action.ANONYMIZE_REQUEST,
                                     note="из админки", user=request.user) for obj in queryset)
        self.message_user(request, f"Обезличено заявок: {done}")

    @admin.display(description="Статус")
    def status_badge(self, obj: Request):
        colours = {
            RequestStatus.NEW: "#6E7A7E",
            RequestStatus.ROUTED: "#0E6E75",
            RequestStatus.CONFIRMED: "#2C6B4C",
            RequestStatus.DONE: "#2C6B4C",
            RequestStatus.CANCELLED: "#9E362E",
            RequestStatus.REJECTED: "#9E362E",
            RequestStatus.SPAM: "#9E362E",
        }
        return format_html(
            '<b style="color:{}">{}</b>', colours.get(obj.status, "#000"), obj.get_status_display()
        )


from apps.hub.models import Contract, ContractTemplate  # noqa: E402


@admin.register(ContractTemplate)
class ContractTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "client_kind", "is_active"]


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ["__str__", "client", "total", "created_at"]
    raw_id_fields = ["client", "request", "work_order"]
    readonly_fields = ["sha256", "created_at"]


from apps.hub.models import Tender, TenderSearch  # noqa: E402


@admin.register(TenderSearch)
class TenderSearchAdmin(admin.ModelAdmin):
    list_display = ["query", "region", "laws", "is_active", "last_run_at", "last_error"]


@admin.register(Tender)
class TenderAdmin(admin.ModelAdmin):
    list_display = ["reg_number", "title", "customer", "price", "deadline", "status"]
    list_filter = ["status", "law"]
    search_fields = ["reg_number", "title", "customer"]
