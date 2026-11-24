from django.contrib import admin

from apps.mailbox.models import FolderState, Message


@admin.register(FolderState)
class FolderStateAdmin(admin.ModelAdmin):
    list_display = ["box", "folder", "imap_name", "uidvalidity", "last_uid", "synced_at", "error"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["received_at", "box", "folder", "direction", "from_addr", "subject", "seen"]
    list_filter = ["box", "folder", "direction", "seen"]
    search_fields = ["subject", "from_addr", "to_addrs"]
    readonly_fields = [f.name for f in Message._meta.fields]
