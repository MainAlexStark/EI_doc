from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tasks"
    verbose_name = "Задачи и наряды"

    def ready(self) -> None:
        from apps.tasks import signals  # noqa: F401
