"""Проверить поиск ЕИС с сервера: что пришло и что разобралось.

    python manage.py tenders_probe "поверка счетчиков воды" --region 43
"""

from django.core.management.base import BaseCommand, CommandError

from apps.hub import tenders
from apps.hub.models import TenderSearch


class Command(BaseCommand):
    help = "Пробный поиск закупок в ЕИС без сохранения"

    def add_arguments(self, parser) -> None:
        parser.add_argument("query")
        parser.add_argument("--region", default="")
        parser.add_argument("--laws", default="44,223")

    def handle(self, *args, **options) -> None:
        search = TenderSearch(query=options["query"], region=options["region"], laws=options["laws"])
        try:
            rows = tenders.fetch(search)
        except tenders.TendersUnavailable as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"разобрано закупок: {len(rows)}")
        for row in rows[:10]:
            self.stdout.write(f"- {row['reg_number']} · {row['price'] or '—'} ₽ · до {row['deadline'] or '—'} · "
                              f"{row['customer'][:60]}\n  {row['title'][:120]}")
        if not rows:
            self.stdout.write("Пусто: либо ничего не найдено, либо формат RSS поменялся — "
                              "проверьте регулярные выражения в apps/hub/tenders.py")
