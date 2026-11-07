from celery import shared_task


@shared_task
def purge_personal_data() -> dict:
    """Обезличить заявки с истёкшим сроком хранения (152-ФЗ). Раз в сутки."""
    from apps.core.personal_data import purge_expired

    return purge_expired()
