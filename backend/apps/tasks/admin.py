from django.contrib import admin

from apps.tasks.models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "assignee", "status", "priority", "due_date", "parent", "recurrence"]
    list_filter = ["status", "priority", "assignee"]
    search_fields = ["title", "description"]
    date_hierarchy = "due_date"
    raw_id_fields = ["parent", "recurrence_parent"]
