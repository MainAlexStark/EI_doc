"""Фоновые задачи поверок."""

from __future__ import annotations

import logging

from celery import shared_task

log = logging.getLogger(__name__)


@shared_task(autoretry_for=(OSError,), retry_backoff=30, max_retries=3)
def issue_protocol_pdf(protocol_id: int) -> bool:
    """Выпустить PDF подписанного протокола. Ошибка вёрстки не отменяет подпись —
    её видно в журнале (нет файла), а файл можно выпустить повторно:
    ``python manage.py issue_protocol_pdfs``."""
    from apps.verification.models import Protocol
    from apps.verification.render import RenderError, issue_protocol_pdf as issue

    protocol = (
        Protocol.objects.select_related("verification__instrument__si_type", "signed_by", "scope__series",
                                        "scope__employee")
        .filter(pk=protocol_id)
        .first()
    )
    if protocol is None:
        return False
    try:
        return issue(protocol)
    except RenderError as exc:
        log.warning("PDF протокола %s не выпущен: %s", protocol.full_number, exc)
        return False
