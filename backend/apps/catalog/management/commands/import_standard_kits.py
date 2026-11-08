"""manage.py import_standard_kits <Перечень применяемых эталонов.docx>

То же, что кнопка «Загрузить новый перечень» на вкладке «Типы СИ» → «Эталоны»: строки
обновляются на месте (по разделу и диапазону), исчезнувшие — выключаются.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.standard_kits import KitError, import_docx


class Command(BaseCommand):
    help = "Загрузить «Перечень применяемых эталонов» из Word"

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"Нет файла {path}")
        try:
            report = import_docx(path.read_bytes())
        except KitError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            "Наборов: новых {kits_created}, обновлено {kits_updated}, выключено {kits_disabled}; "
            "вспомогательных СИ: {aux} (выключено {aux_disabled})".format(**report)
        ))
