"""Посмотреть фактический ответ открытого реестра ФИФ.

Схема ответа публично не документирована и менялась. Эта команда запускается
на сервере (из среды разработки реестр недоступен) и показывает, что реально
приходит, — после чего FIELD_ALIASES в apps/arshin/fif.py уточняется под
фактические имена полей.

    python manage.py fif_probe --query СВК
    python manage.py fif_probe --query 12345-06 --raw
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.arshin.fif import FIELD_ALIASES, FifClient, FifUnavailable


class Command(BaseCommand):
    help = "Пробный запрос к открытому реестру ФИФ ОЕИ"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--query", required=True, help="номер в Госреестре или часть названия")
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--raw", action="store_true", help="показать ответ целиком")

    def handle(self, *args, **options) -> None:
        client = FifClient()
        self.stdout.write(f"Реестр: {client.base_url}")

        try:
            payload = client.raw_search(options["query"], limit=options["limit"])
        except FifUnavailable as exc:
            self.stdout.write(self.style.ERROR(str(exc)))
            return

        if options["raw"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2)[:20000])
            return

        rows = list(FifClient._rows(payload))
        self.stdout.write(self.style.MIGRATE_HEADING(f"Строк в ответе: {len(rows)}"))
        if not rows:
            self.stdout.write("Ответ не распознан, запустите с --raw")
            return

        self.stdout.write(self.style.MIGRATE_HEADING("Ключи первой строки:"))
        for key, value in rows[0].items():
            preview = str(value)
            if len(preview) > 70:
                preview = preview[:67] + "…"
            self.stdout.write(f"  {key:<28} {preview}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Как это разобралось:"))
        for candidate in (FifClient._parse(row) for row in rows):
            self.stdout.write(f"  {candidate.label}")

        unmatched = [
            our_name
            for our_name, aliases in FIELD_ALIASES.items()
            if FifClient._pick(rows[0], aliases) in (None, "")
        ]
        if unmatched:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Не нашли соответствия для полей: "
                    + ", ".join(unmatched)
                    + ".\nДобавьте фактические имена в FIELD_ALIASES (apps/arshin/fif.py)."
                )
            )
