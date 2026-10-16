from django.urls import path

from apps.assistant import api

urlpatterns = [
    path("conversations/", api.ConversationListView.as_view(), name="assistant_conversations"),
    path("conversations/<int:pk>/", api.ConversationDetailView.as_view(), name="assistant_conversation"),
    path("conversations/<int:pk>/messages/", api.SendMessageView.as_view(), name="assistant_send"),
    path("conversations/<int:pk>/regenerate/", api.RegenerateView.as_view(), name="assistant_regenerate"),
    path("runs/<int:pk>/", api.RunStateView.as_view(), name="assistant_run"),
    path("runs/<int:pk>/cancel/", api.RunCancelView.as_view(), name="assistant_run_cancel"),
    path(
        "actions/<int:pk>/<str:decision>/", api.ActionDecideView.as_view(), name="assistant_action_decide",
    ),
    path("attachments/", api.AttachmentUploadView.as_view(), name="assistant_attachment_upload"),
    path(
        "attachments/<int:pk>/download/", api.AttachmentDownloadView.as_view(),
        name="assistant_attachment_download",
    ),
    path("models/", api.ModelsView.as_view(), name="assistant_models"),
    path("knowledge/", api.KnowledgeListView.as_view(), name="assistant_knowledge"),
    path("knowledge/search/", api.KnowledgeSearchView.as_view(), name="assistant_knowledge_search"),
    path("knowledge/<int:pk>/", api.KnowledgeDetailView.as_view(), name="assistant_knowledge_detail"),
    path(
        "knowledge/<int:pk>/download/", api.KnowledgeDownloadView.as_view(),
        name="assistant_knowledge_download",
    ),
    path("memory/", api.MemoryListView.as_view(), name="assistant_memory"),
    path("memory/<int:pk>/", api.MemoryDetailView.as_view(), name="assistant_memory_detail"),
    path("mcp/", api.McpListView.as_view(), name="assistant_mcp"),
    path("mcp/<int:pk>/", api.McpDetailView.as_view(), name="assistant_mcp_detail"),
    path("mcp/<int:pk>/tools/", api.McpToolsView.as_view(), name="assistant_mcp_tools"),
    path("settings/", api.SettingsView.as_view(), name="assistant_settings"),
    path("usage/", api.UsageView.as_view(), name="assistant_usage"),
]
