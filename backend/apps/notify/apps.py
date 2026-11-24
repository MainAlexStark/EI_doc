from django.apps import AppConfig


class NotifyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notify"
    verbose_name = "Уведомления и почта"

    def ready(self) -> None:
        from apps.notify import signals  # noqa: F401
