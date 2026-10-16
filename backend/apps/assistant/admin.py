from django.contrib import admin

from apps.assistant.models import (
    AssistantSettings,
    Conversation,
    KnowledgeFile,
    McpServer,
    MemoryItem,
    PendingAction,
    Run,
)


@admin.register(AssistantSettings)
class AssistantSettingsAdmin(admin.ModelAdmin):
    list_display = ["__str__", "default_model", "max_steps", "updated_at"]


@admin.register(McpServer)
class McpServerAdmin(admin.ModelAdmin):
    list_display = ["name", "title", "url", "enabled"]


@admin.register(KnowledgeFile)
class KnowledgeFileAdmin(admin.ModelAdmin):
    list_display = ["title", "category", "scope", "mode", "chars", "updated_at"]
    list_filter = ["category", "scope", "mode"]
    search_fields = ["title", "description"]
    exclude = ["text"]


@admin.register(MemoryItem)
class MemoryItemAdmin(admin.ModelAdmin):
    list_display = ["text", "scope", "user", "updated_at"]
    list_filter = ["scope"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "model", "updated_at"]
    search_fields = ["title"]


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ["pk", "conversation", "model", "status", "created_at", "finished_at"]
    list_filter = ["status"]


@admin.register(PendingAction)
class PendingActionAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "status", "decided_by", "decided_at"]
    list_filter = ["kind", "status"]
