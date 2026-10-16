from django.apps import AppConfig


class AssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assistant"
    verbose_name = "Ассистент-метролог"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        from apps.assistant.setup import ensure_rst_server

        post_migrate.connect(ensure_rst_server, sender=self)
