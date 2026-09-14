"""Демо-данные, чтобы посмотреть журнал и нормоконтроль живьём.

    python manage.py seed_catalog
    python manage.py seed_demo
    python manage.py runserver

Заводит поверителя (metrolog@ei.test / demo12345), шесть поверок с измерениями,
присваивает номера и подписывает часть протоколов. Одна поверка приходит «со
скана» с низкой уверенностью — чтобы было видно, как она ждёт сверки.

Только для разработки: команда отказывается работать с DEBUG=False.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import MeasurementFamily, SiType, Standard, VerificationMethod
from apps.core.models import Attestation, Employee, Role, User
from apps.verification import measurements as service
from apps.verification import numbering
from apps.verification.calculators import water_meter as wm
from apps.verification.models import Client, Instrument, Protocol, Site, Verification

EMAIL = "metrolog@ei.test"
PASSWORD = "demo12345"

LIMITS = {
    "q_min": "0.03", "q_transition_a": "0.15", "q_transition_b": "0.12",
    "q_nominal": "1.5", "q_max": "3",
    "error_below_transition": "5", "error_above_transition": "2",
}
MODES = (("0.030", "0.0063", 720), ("0.132", "0.0142", 360), ("0.666", "0.0226", 120))
ADDRESSES = [
    "г.Киров, ул.Ленинградская 1а-70",
    "г.Киров, ул.Заводская 6-95",
    "г.Киров, ул.Московская 217-991",
    "г.Слободской, ул.Советская 12-4",
]
SERIALS = ["12470437", "13429206", "1189735", "1196273", "8308739К19", "40238491"]


class Command(BaseCommand):
    help = "Демо-данные для журнала и нормоконтроля (только для разработки)"

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("Демо-данные заводятся только при DEBUG=True")

        family = MeasurementFamily.objects.filter(code="water-meter").first()
        if family is None:
            raise CommandError("Сначала выполните: python manage.py seed_catalog")

        si_type = SiType.objects.filter(name="СХВ-15", registry_number="16078-13").first()
        if si_type is None:
            raise CommandError("В справочнике нет типа СХВ-15 16078-13")
        si_type.limits = LIMITS
        si_type.save(update_fields=["limits"])

        employee = self._employee(family)
        method, _ = VerificationMethod.objects.get_or_create(
            designation="«Рекомендация. ГСИ. Счетчики воды. Методика поверки» МИ 1592-2015"
        )
        standard, _ = Standard.objects.get_or_create(
            fif_number="72850.18.3Р.00701885",
            defaults={"name": "УПСЖ 5П", "serial_number": "2350",
                      "verified_at": dt.date(2026, 1, 10),
                      "valid_until": dt.date(2027, 1, 10)},
        )
        client, _ = Client.objects.get_or_create(name="Частное лицо")

        created = 0
        for index, serial in enumerate(SERIALS):
            if Instrument.objects.filter(si_type=si_type, serial_number=serial).exists():
                continue
            self._verification(
                index, serial, si_type, employee, method, standard, client, family
            )
            created += 1

        if created:
            scope = numbering.scope_for(Verification.objects.latest("id"))
            result = numbering.assign_numbers(scope)
            signed = 0
            for protocol in Protocol.objects.filter(scope=scope).exclude(seq=None).order_by("seq")[:2]:
                numbering.seal(protocol, signed_by=employee)
                signed += 1
            self.stdout.write(
                f"  поверок заведено: {created}, номеров присвоено: {len(result.assigned)}, "
                f"подписано: {signed}"
            )
        else:
            self.stdout.write("  демо-данные уже заведены")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Вход: {EMAIL} / {PASSWORD}"))

    # -- части ------------------------------------------------------------
    def _employee(self, family) -> Employee:
        user, _ = User.objects.get_or_create(
            email=EMAIL, defaults={"role": Role.METROLOGIST, "is_staff": True}
        )
        user.set_password(PASSWORD)
        user.save()

        employee, _ = Employee.objects.get_or_create(
            tab_number="02",
            defaults={"full_name": "Стариков Сергей Владимирович",
                      "short_name": "Стариков С.В."},
        )
        employee.user = user
        employee.save()

        if not employee.attestations.exists():
            attestation = Attestation.objects.create(
                employee=employee, document="ААА 0001",
                valid_from=dt.date(2024, 1, 1), valid_to=dt.date(2030, 1, 1),
            )
            attestation.families.add(family)
        return employee

    def _verification(self, index, serial, si_type, employee, method, standard, client, family):
        site, _ = Site.objects.get_or_create(address=ADDRESSES[index % len(ADDRESSES)])
        instrument = Instrument.objects.create(
            si_type=si_type, serial_number=serial, owner=client, site=site,
            manufacture_year=2014 + index % 5,
        )
        day = 3 + index
        verification = Verification.objects.create(
            instrument=instrument, verifier=employee, method=method,
            verified_at=timezone.make_aware(dt.datetime(2026, 8, day, 10, 0)),
            next_verification_date=dt.date(2032, 8, max(day - 1, 1)),
            temperature=Decimal("21"), humidity=Decimal("53.1"), pressure=Decimal("100"),
        )
        verification.standards.add(standard)

        reading = Decimal("1091.763") + index
        rows = []
        for flow_rate, volume_standard, seconds in MODES:
            volume = wm.excel_roundup(Decimal(flow_rate) * seconds / Decimal(3600), 3)
            rows.append({
                "flow_rate": flow_rate, "volume_standard": volume_standard,
                "reading_start": str(reading), "reading_end": str(reading + volume),
            })
            reading += volume + Decimal("0.006")

        payload = {
            "layout": wm.LAYOUT_COMPACT, "meter_class": wm.CLASS_B, "unit_type": "х/в",
            "water_temperature": "6", "pulse_weight": "0.00817", "rows": rows,
        }
        # Одна поверка приходит со скана и ждёт сверки — чтобы это было видно.
        if index == 2:
            payload["source"] = service.SOURCE_SCAN
            payload["rows"] = [
                row | {"confidence": 0.55 if number == 1 else 0.97}
                for number, row in enumerate(rows)
            ]

        service.apply(verification, payload)
        Protocol.objects.create(verification=verification, scope=numbering.scope_for(verification))
