from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Пользователи и сотрудники"

    def ready(self) -> None:
        from apps.core import permissions  # noqa: F401 — регистрирует права ролей
