from django.urls import path

from apps.mailbox import api

urlpatterns = [
    path("status/", api.StatusView.as_view(), name="mail_status"),
    path("sync/", api.SyncView.as_view(), name="mail_sync"),
    path("dns/", api.DnsView.as_view(), name="mail_dns"),
    path("messages/", api.MessageListView.as_view(), name="mail_messages"),
    path("messages/<int:pk>/", api.MessageDetailView.as_view(), name="mail_message"),
    path("messages/<int:pk>/action/", api.MessageActionView.as_view(), name="mail_action"),
    path("messages/<int:pk>/body/", api.MessageBodyView.as_view(), name="mail_body"),
    path("messages/<int:pk>/att/<int:n>/", api.AttachmentView.as_view(), name="mail_attachment"),
    path("messages/<int:pk>/raw/", api.RawView.as_view(), name="mail_raw"),
    path("compose/", api.ComposeView.as_view(), name="mail_compose"),
    path("send/", api.SendView.as_view(), name="mail_send"),
]
