from django.contrib import admin

from apps.notify.outbox import SECRET_TEMPLATES
from apps.notify.models import NotifyEvent, NotifyPrefs, OutboxMail


@admin.register(OutboxMail)
class OutboxMailAdmin(admin.ModelAdmin):
    list_display = ["created_at", "to", "subject", "status", "attempts", "sent_at"]
    list_filter = ["status", "template"]
    search_fields = ["to", "subject"]
    readonly_fields = [f.name for f in OutboxMail._meta.fields if f.name not in ("text", "html")] + [
        "text_shown", "html_shown",
    ]
    exclude = ["text", "html"]

    # Письмо сброса пароля несёт живую ссылку: кто видит очередь, мог бы задать
    # пароль за другого (в т.ч. за владельца). Тело таких писем не показываем.
    @admin.display(description="текст")
    def text_shown(self, obj):
        return "скрыто: ссылка для сброса пароля" if obj.template in SECRET_TEMPLATES else obj.text

    @admin.display(description="HTML")
    def html_shown(self, obj):
        return "скрыто" if obj.template in SECRET_TEMPLATES else obj.html


@admin.register(NotifyEvent)
class NotifyEventAdmin(admin.ModelAdmin):
    list_display = ["created_at", "user", "category", "kind", "object_id", "dispatched_at"]
    list_filter = ["category", "kind"]


@admin.register(NotifyPrefs)
class NotifyPrefsAdmin(admin.ModelAdmin):
    list_display = ["user", "requests", "assignments", "review", "reminders"]
