"""Прочитать текст документов закупок, у которых его нет (загружены до чтения текста или с ошибкой).

    python manage.py extract_procurement_texts            # только непрочитанные
    python manage.py extract_procurement_texts --all      # все заново
"""

from django.core.management.base import BaseCommand

from apps.logistics import procurement_docs
from apps.logistics.models import ProcurementDocument, TextStatus


class Command(BaseCommand):
    help = "Извлечь текст документов закупок для поиска и ассистента"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Перечитать и уже прочитанные")

    def handle(self, *args, **options):
        qs = ProcurementDocument.objects.exclude(file="")
        if not options["all"]:
            qs = qs.exclude(text_status=TextStatus.DONE)
        done = 0
        for doc in qs.iterator():
            procurement_docs.extract(doc)
            done += 1
            self.stdout.write(f"{doc.pk} {doc.title}: {doc.get_text_status_display()} {doc.text_error}")
        self.stdout.write(self.style.SUCCESS(f"Обработано документов: {done}"))
