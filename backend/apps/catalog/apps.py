from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    verbose_name = "Справочники"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from apps.catalog.models import MeasurementFamily, PricingSettings

        def drop_family_options(**kwargs) -> None:
            from apps.catalog.api import FAMILY_OPTIONS_KEY
            from apps.core import cache

            cache.forget(FAMILY_OPTIONS_KEY)

        for model in (MeasurementFamily, PricingSettings):
            post_save.connect(drop_family_options, sender=model, dispatch_uid=f"family-options-{model.__name__}")
            post_delete.connect(drop_family_options, sender=model, dispatch_uid=f"family-options-del-{model.__name__}")
