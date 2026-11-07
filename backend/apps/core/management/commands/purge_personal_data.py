"""Обезличить персональные данные с истёкшим сроком хранения.

    python manage.py purge_personal_data --dry-run   сколько попадёт
    python manage.py purge_personal_data
"""

from django.core.management.base import BaseCommand

from apps.core.personal_data import purge_expired


class Command(BaseCommand):
    help = "Обезличить ПДн с истёкшим сроком хранения (152-ФЗ)"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options) -> None:
        result = purge_expired(dry_run=options["dry_run"])
        prefix = "попадёт под обезличивание" if options["dry_run"] else "обезличено"
        self.stdout.write(f"{prefix}: отклонённых/спам {result['rejected']}, прочих {result['finished']}")
