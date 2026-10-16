"""Документы утверждённого типа из ФГИС: зарегистрировать ссылки и скачать файлы к себе."""

from __future__ import annotations

import logging

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.arshin.fif import FifCard, FifClient, FifUnavailable
from apps.arshin.models import FifDocument

logger = logging.getLogger(__name__)

MAX_SIZE = 40 * 1024 * 1024


def register_documents(card: FifCard) -> list[FifDocument]:
    """Завести записи о документах карточки (без скачивания)."""
    documents = []
    for ref in card.documents:
        doc, _ = FifDocument.objects.update_or_create(
            doc_uuid=ref.doc_uuid,
            defaults={
                "mit_uuid": card.uuid,
                "registry_number": card.registry_number,
                "kind": ref.kind,
                "title": ref.title[:255],
                "url": ref.url,
            },
        )
        documents.append(doc)
    return documents


def download(doc: FifDocument, *, client: FifClient | None = None, force: bool = False) -> FifDocument:
    """Скачать файл документа к себе. Ошибку запоминаем, а не роняем вызывающего."""
    if doc.file and not force:
        return doc
    client = client or FifClient()
    try:
        content, filename, content_type = client.download(doc.url)
    except FifUnavailable as exc:
        doc.error = str(exc)[:255]
        doc.save(update_fields=["error"])
        logger.warning("Документ %s не скачан: %s", doc.url, exc)
        return doc
    if len(content) > MAX_SIZE:
        doc.error = "Файл больше 40 МБ — открывайте по ссылке в ФГИС"
        doc.save(update_fields=["error"])
        return doc

    safe_name = f"{doc.registry_number or doc.mit_uuid}_{doc.kind}_{doc.doc_uuid[:8]}"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ("pdf" if "pdf" in content_type else "bin")
    doc.file.save(f"{safe_name}.{extension}", ContentFile(content), save=False)
    doc.filename = filename[:255]
    doc.content_type = content_type[:100]
    doc.size = len(content)
    doc.downloaded_at = timezone.now()
    doc.error = ""
    doc.save()
    return doc


def fetch_type_documents(mit_uuid: str) -> list[FifDocument]:
    """Карточка → записи о документах → файлы. Для Celery и ручного «скачать ещё раз»."""
    client = FifClient()
    try:
        card = client.get_type(mit_uuid)
    except FifUnavailable as exc:
        logger.warning("Карточка %s недоступна: %s", mit_uuid, exc)
        return list(FifDocument.objects.filter(mit_uuid=mit_uuid))
    return [download(doc, client=client) for doc in register_documents(card)]
