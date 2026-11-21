from celery import shared_task


@shared_task(name="apps.logistics.tasks.extract_procurement_document", soft_time_limit=5 * 60, time_limit=6 * 60)
def extract_procurement_document(doc_id: int) -> None:
    """Текст документа закупки (pdf/docx/xlsx) — для поиска и ассистента."""
    from apps.logistics.procurement_docs import extract_by_id

    extract_by_id(doc_id)
