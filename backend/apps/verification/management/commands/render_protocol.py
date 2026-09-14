"""Собрать PDF протокола — для проверки шаблона глазами.

    python manage.py render_protocol --verification 12 --out /tmp/protocol.pdf
    python manage.py render_protocol --verification 12 --context   # только данные

Вёрстка повторяет прежний документ из `.xlsm`; менять её без согласования
с метрологом нельзя.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.verification import render
from apps.verification.models import Verification


class Command(BaseCommand):
    help = "Сверстать PDF протокола по поверке"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--verification", type=int, required=True, help="id поверки")
        parser.add_argument("--out", default="protocol.pdf")
        parser.add_argument("--number", default=None, help="номер, если протокол ещё не заведён")
        parser.add_argument("--context", action="store_true", help="показать данные и выйти")

    def handle(self, *args, **options) -> None:
        try:
            verification = Verification.objects.select_related(
                "instrument__si_type", "verifier"
            ).get(pk=options["verification"])
        except Verification.DoesNotExist as exc:
            raise CommandError(f"Поверка {options['verification']} не найдена") from exc

        number = options["number"]
        if not number:
            protocol = getattr(verification, "protocol", None)
            number = protocol.full_number if protocol else ""
            if not number:
                raise CommandError(
                    "У поверки нет протокола с номером. Присвойте номер или задайте --number"
                )

        try:
            context = render.build_context(verification, number=number)
        except render.RenderError as exc:
            raise CommandError(str(exc)) from exc

        if options["context"]:
            self.stdout.write(json.dumps(context, ensure_ascii=False, indent=1))
            return

        if not render.typst_available():
            raise CommandError(
                "Не найден бинарник typst. В образе он ставится Dockerfile'ом; "
                "локально — https://github.com/typst/typst/releases"
            )

        try:
            pdf = render.render_pdf(context)
        except render.RenderError as exc:
            raise CommandError(str(exc)) from exc

        out = Path(options["out"]).expanduser()
        out.write_bytes(pdf)
        self.stdout.write(self.style.SUCCESS(f"{number} → {out} ({len(pdf) // 1024} КБ)"))
