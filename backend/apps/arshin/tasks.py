"""Фоновые задачи ФГИС: скачивание документов типа не держит запрос поверителя."""

from __future__ import annotations

from celery import shared_task

from apps.arshin.documents import fetch_type_documents


@shared_task
def download_type_documents(mit_uuid: str) -> int:
    return sum(1 for doc in fetch_type_documents(mit_uuid) if doc.file)
