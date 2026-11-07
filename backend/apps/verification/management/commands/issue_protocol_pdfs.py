"""Выпустить PDF подписанных протоколов, у которых файла ещё нет.

    python manage.py issue_protocol_pdfs            все без файла
    python manage.py issue_protocol_pdfs --limit 50
"""

from django.core.management.base import BaseCommand

from apps.verification.models import Protocol, ProtocolStatus
from apps.verification.tasks import issue_protocol_pdf


class Command(BaseCommand):
    help = "Выпустить PDF подписанных протоколов без файла"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options) -> None:
        qs = Protocol.objects.filter(
            status__in=[ProtocolStatus.SIGNED, ProtocolStatus.PUBLISHED], pdf=""
        ).order_by("pk").values_list("pk", flat=True)
        if options["limit"]:
            qs = qs[: options["limit"]]
        done = sum(1 for pk in qs if issue_protocol_pdf(pk))
        self.stdout.write(f"выпущено: {done}")
