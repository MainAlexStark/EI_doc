from django.apps import AppConfig


class MailboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mailbox"
    verbose_name = "Почта фирмы"

    def ready(self) -> None:
        from apps.mailbox import signals  # noqa: F401
