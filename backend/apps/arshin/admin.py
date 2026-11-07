from django.contrib import admin

from apps.arshin.models import FifDocument


@admin.register(FifDocument)
class FifDocumentAdmin(admin.ModelAdmin):
    list_display = ["registry_number", "kind", "title", "downloaded_at", "size", "error"]
    list_filter = ["kind"]
    search_fields = ["registry_number", "mit_uuid", "title"]


from apps.arshin.models import ArshinSubmission, ArshinSubmissionItem  # noqa: E402


class ArshinSubmissionItemInline(admin.TabularInline):
    model = ArshinSubmissionItem
    extra = 0
    raw_id_fields = ["protocol"]
    readonly_fields = ["updated_at"]


@admin.register(ArshinSubmission)
class ArshinSubmissionAdmin(admin.ModelAdmin):
    list_display = ["pk", "status", "created_at", "uploaded_at", "reconciled_at", "created_by"]
    list_filter = ["status"]
    readonly_fields = ["sha256", "created_at"]
    inlines = [ArshinSubmissionItemInline]
