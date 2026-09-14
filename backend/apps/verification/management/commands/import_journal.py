"""Импорт исторического журнала учёта поверочных работ из .xlsx.

Старое приложение читало журнал по индексам столбцов (values[32], row[47]).
Здесь маппинг идёт ПО ЗАГОЛОВКАМ и живёт в отдельном JSON-файле, поэтому
перестановка столбцов в исходном файле ничего не ломает.

Порядок работы:

    # 1. посмотреть, какие заголовки есть в файле
    python manage.py import_journal --file journal.xlsx --inspect

    # 2. заполнить mapping.json по образцу, который напечатает --inspect
    # 3. прогон вхолостую: ничего не пишет, показывает, что не разобралось
    python manage.py import_journal --file journal.xlsx --mapping mapping.json --dry-run

    # 4. боевой импорт
    python manage.py import_journal --file journal.xlsx --mapping mapping.json
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

FIELDS = {
    "protocol_number": "Номер протокола (ЕИ-03-02-00767)",
    "verified_at": "Дата поверки СИ",
    "next_verification_date": "Дата следующей поверки",
    "si_name": "Модификация СИ — наименование, которое печатается в протоколе",
    "si_registry_number": "Регистрационный номер типа СИ в Госреестре",
    "serial_number": "Заводской номер СИ",
    "manufacture_year": "Год выпуска",
    "owner": "Собственник",
    "address": "Адрес поверки — лежит в графе «Владелец СИ» формата ФИФ",
    "verifier_name": "Ф.И.О. поверителя",
    "method": "Наименование документа, на основании которого выполняется поверка",
    "standards": "Номера по реестру СИ, применяемых в качестве эталона",
    "suitable": "Пригодность СИ (Пригодно / Непригодно)",
    "unsuitability_reason": "Причины непригодности",
    "temperature": "Температура",
    "pressure": "Атмосферное давление",
    "humidity": "Относительная влажность",
    "readings": "Показания",
    "unit_type": "Тип (г/в или х/в) — только у счётчиков воды",
    "other_info": "Прочие сведения",
}


def normalize_header(value) -> str:
    """Первая строка заголовка, без лишних пробелов, точек и регистра.

    Заголовки журнала — это многострочные описания полей формата ФИФ
    («Дата поверки СИ.\\nФормат: Дата, excel должен понимать…»), поэтому
    сопоставляем по первой строке, а не по всему тексту.
    """
    if value is None:
        return ""
    first_line = str(value).split("\n")[0]
    return " ".join(first_line.split()).strip().strip(".").lower()


class Command(BaseCommand):
    help = "Импорт исторического журнала поверок из Excel по заголовкам столбцов"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--file", required=True, help="путь к .xlsx")
        parser.add_argument("--sheet", default=None, help="лист (по умолчанию первый)")
        parser.add_argument("--mapping", default=None, help="JSON: поле -> заголовок столбца")
        parser.add_argument("--inspect", action="store_true", help="показать заголовки и выйти")
        parser.add_argument("--dry-run", action="store_true", help="не писать в базу")
        parser.add_argument("--limit", type=int, default=0, help="обработать только N строк")

    def handle(self, *args, **options) -> None:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover
            raise CommandError("Нужен openpyxl: pip install openpyxl") from exc

        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"Файл не найден: {path}")

        workbook = load_workbook(filename=path, data_only=True, read_only=True)
        sheet = workbook[options["sheet"]] if options["sheet"] else workbook.worksheets[0]

        rows = sheet.iter_rows(values_only=True)
        header = [str(cell).strip() if cell is not None else "" for cell in next(rows)]
        # Заголовки в журнале дублируются (несколько пустых «None» в хвосте),
        # поэтому индекс строим по первому вхождению.

        if options["inspect"]:
            self._inspect(sheet.title, header)
            return

        if not options["mapping"]:
            raise CommandError("Нужен --mapping или --inspect")
        mapping = json.loads(Path(options["mapping"]).read_text(encoding="utf-8"))

        index = self._build_index(header, mapping)
        self._import(rows, index, dry_run=options["dry_run"], limit=options["limit"])

    # -- шаги -----------------------------------------------------------
    def _inspect(self, sheet_title: str, header: list[str]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING(f"Лист «{sheet_title}», столбцов: {len(header)}"))
        for position, title in enumerate(header):
            if title:
                self.stdout.write(f"  {position:>3}  {normalize_header(title)}")
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Заготовка mapping.json — подставьте заголовки:"))
        template = {field: "" for field in FIELDS}
        self.stdout.write(json.dumps(template, ensure_ascii=False, indent=2))
        self.stdout.write("")
        for field, description in FIELDS.items():
            self.stdout.write(f"  {field:<24} — {description}")

    def _build_index(self, header: list[str], mapping: dict[str, str]) -> dict[str, int]:
        lookup: dict[str, int] = {}
        for position, title in enumerate(header):
            key = normalize_header(title)
            if key:
                lookup.setdefault(key, position)

        index: dict[str, int] = {}
        missing: list[str] = []
        for field, title in mapping.items():
            if not title or field.startswith("_"):
                continue  # ключи на подчёркивании — комментарии в файле маппинга
            position = lookup.get(normalize_header(title))
            if position is None:
                missing.append(f"{field}: «{title}»")
            else:
                index[field] = position
        if missing:
            raise CommandError("Столбцы не найдены в файле:\n  " + "\n  ".join(missing))
        return index

    def _import(self, rows, index: dict[str, int], *, dry_run: bool, limit: int) -> None:
        from apps.verification.models import ConditionSource

        stats: Counter[str] = Counter()
        problems: list[str] = []

        with transaction.atomic():
            for number, raw in enumerate(rows, start=2):
                if limit and stats["read"] >= limit:
                    break
                stats["read"] += 1
                if not any(raw):
                    stats["empty"] += 1
                    continue

                record = {field: raw[position] for field, position in index.items() if position < len(raw)}
                try:
                    self._validate(record)
                except ValueError as exc:
                    stats["rejected"] += 1
                    problems.append(f"строка {number}: {exc}")
                    continue

                # Историю условий поверки восстановить нельзя: в старом журнале
                # измеренные значения ничем не отличались от подставленных
                # случайно. Всё импортированное помечается как archive —
                # честнее, чем выдать за измеренное.
                record["_condition_source"] = ConditionSource.ARCHIVE
                stats["ok"] += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Итог импорта"))
        for key in ("read", "ok", "rejected", "empty"):
            self.stdout.write(f"  {key:<10} {stats[key]}")
        if problems:
            self.stdout.write(self.style.WARNING(f"\nНе перенесено строк: {len(problems)}"))
            for line in problems[:50]:
                self.stdout.write(f"  {line}")
            if len(problems) > 50:
                self.stdout.write(f"  … и ещё {len(problems) - 50}")
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN — в базу ничего не записано."))

    @staticmethod
    def _validate(record: dict) -> None:
        required = ("protocol_number", "verified_at", "serial_number", "si_name")
        empty = [field for field in required if not record.get(field)]
        if empty:
            raise ValueError("пустые обязательные поля: " + ", ".join(empty))

        moment = record["verified_at"]
        if isinstance(moment, str):
            try:
                moment = dt.datetime.strptime(moment.strip(), "%d.%m.%Y")
            except ValueError as exc:
                raise ValueError(f"не разобрана дата поверки: {moment!r}") from exc
        if isinstance(moment, dt.date) and not isinstance(moment, dt.datetime):
            moment = dt.datetime.combine(moment, dt.time(10, 0))
        if not isinstance(moment, dt.datetime):
            raise ValueError(f"не разобрана дата поверки: {record['verified_at']!r}")
        record["verified_at"] = timezone.make_aware(moment) if timezone.is_naive(moment) else moment
