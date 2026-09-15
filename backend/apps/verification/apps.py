from django.apps import AppConfig


class VerificationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.verification"
    verbose_name = "Поверки и протоколы"

    def ready(self) -> None:
        from apps.verification import signals  # noqa: F401
