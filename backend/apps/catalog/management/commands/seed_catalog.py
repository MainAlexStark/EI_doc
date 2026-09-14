"""Первичное наполнение справочников из рабочих данных фирмы.

    python manage.py seed_catalog

Заводит серии нумерации, семейства СИ и 60 типов счётчиков воды с
метрологическими характеристиками. Характеристики вынуты из таблицы K54:W98
листа «Данные» рабочих шаблонов `.xlsm` — того самого справочника, по которому
Excel подставлял пределы погрешности в протокол.

Команда идемпотентна: повторный запуск обновляет, но не дублирует.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import MeasurementFamily, NumberingSeries, SiType

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "water_meter_types.json"

SERIES = [
    # Код 03 — основная серия. Вопреки ожиданию, она НЕ про счётчики воды:
    # в журнале за 2021–2026 в ней идут и весы, и гири, и дозаторы.
    {"code": "03", "name": "Основная", "is_default": True},
    {"code": "01", "name": "Манометры (отдельная серия с 2023 г.)", "is_default": False},
]

FAMILIES = [
    {"code": "water-meter", "name": "Счётчики воды", "calculator_key": "water_meter"},
    {"code": "scales", "name": "Весы", "calculator_key": ""},
    {"code": "weights", "name": "Гири", "calculator_key": ""},
    {"code": "manometers", "name": "Манометры", "calculator_key": "", "series": "01"},
]


class Command(BaseCommand):
    help = "Завести серии нумерации, семейства СИ и типы счётчиков воды"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--quiet", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        quiet = options["quiet"]

        series_by_code = {}
        for item in SERIES:
            series, created = NumberingSeries.objects.update_or_create(
                code=item["code"],
                defaults={"name": item["name"], "is_default": item["is_default"],
                          "digits": 5, "resets_yearly": True},
            )
            series_by_code[item["code"]] = series
            if not quiet:
                self.stdout.write(f"  серия {series} {'создана' if created else 'обновлена'}")

        families = {}
        for item in FAMILIES:
            family, created = MeasurementFamily.objects.update_or_create(
                code=item["code"],
                defaults={
                    "name": item["name"],
                    "calculator_key": item["calculator_key"],
                    "numbering_series": series_by_code.get(item.get("series", "03")),
                },
            )
            families[item["code"]] = family
            if not quiet:
                self.stdout.write(f"  семейство «{family.name}» {'создано' if created else 'обновлено'}")

        water = families["water-meter"]
        types = json.loads(FIXTURE.read_text(encoding="utf-8"))
        created_count = updated_count = 0
        for item in types:
            _, created = SiType.objects.update_or_create(
                registry_number=item["registry_number"],
                name=item["name"],
                defaults={"family": water, "limits": item["limits"], "is_active": True},
            )
            created_count += created
            updated_count += not created

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Типов счётчиков воды: создано {created_count}, обновлено {updated_count}"
            )
        )

        # Ровно та проблема, ради которой делалась подсказка из ФИФ:
        # одно наименование у нескольких регистрационных номеров.
        by_name: dict[str, set[str]] = {}
        for item in types:
            by_name.setdefault(item["name"], set()).add(item["registry_number"])
        ambiguous = {name: sorted(nums) for name, nums in by_name.items() if len(nums) > 1}
        if ambiguous:
            self.stdout.write(
                self.style.WARNING(
                    f"Наименований с несколькими регистрационными номерами: {len(ambiguous)}. "
                    "Выбирать такие типы можно только вместе с изготовителем."
                )
            )
            for name, numbers in list(ambiguous.items())[:5]:
                self.stdout.write(f"    {name}: {', '.join(numbers)}")
