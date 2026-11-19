"""Типы СИ, которые завелись счётчиками воды по ошибке, — в своё семейство.

До 19.11.2026 любой тип, вид которого не угадался по названию, попадал в «Счётчики воды»
(в выборе «Вид СИ» было только это семейство). Команда находит такие типы: в семействе
счётчиков воды, а по наименованию типа из Госреестра — не счётчик воды.

    python manage.py fix_si_type_families            # только показать
    python manage.py fix_si_type_families --apply    # перенести

Куда: семейство, которое однозначно угадывается по наименованию, иначе «Прочие СИ».
Поверки, уже проведённые калькулятором воды, не трогаются — меняется только тип.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.api_types import WATER_CALCULATOR, default_family
from apps.catalog.models import SiType
from apps.catalog.standard_kits import guess_families

WATER_WORDS = ("вод", "тепл")  # «счётчики воды», «водосчётчики», «теплосчётчики» — не трогаем


class Command(BaseCommand):
    help = "Перенести типы СИ, ошибочно заведённые счётчиками воды, в своё семейство"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Перенести (без флага — только показать)")

    def handle(self, *args, apply=False, **options):
        moved = 0
        for si_type in SiType.objects.filter(family__calculator_key=WATER_CALCULATOR).select_related("family"):
            text = f"{si_type.type_title} {si_type.name}".lower().replace("ё", "е")
            if not si_type.type_title.strip() or any(w in text for w in WATER_WORDS):
                continue
            found = [f for f in guess_families(text) if f.calculator_key != WATER_CALCULATOR]
            target = found[0] if len(found) == 1 else default_family()
            self.stdout.write(f"{si_type.registry_number} «{si_type.name}» ({si_type.type_title[:80]}) → {target.name}")
            if apply:
                with transaction.atomic():
                    si_type.family = target
                    si_type.save(update_fields=["family"])
            moved += 1
        verb = "Перенесено" if apply else "Нашлось (запустите с --apply, чтобы перенести)"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {moved}"))
