"""Фоновые задачи hub."""

from celery import shared_task


@shared_task
def search_tenders() -> int:
    """Госзакупки: прогнать сохранённые поиски ЕИС (три раза в день)."""
    from apps.hub.tenders import run_all

    return run_all()
