from django.contrib import admin

from apps.arshin.models import FifDocument


@admin.register(FifDocument)
class FifDocumentAdmin(admin.ModelAdmin):
    list_display = ["registry_number", "kind", "title", "downloaded_at", "size", "error"]
    list_filter = ["kind"]
    search_fields = ["registry_number", "mit_uuid", "title"]
