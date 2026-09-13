"""Минимальные фабрики для тестов нумерации."""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Attestation, Employee
from apps.verification.models import Instrument, Protocol, ProtocolStatus, Verification
from apps.verification.numbering import scope_for


def make_family(*, code: str = "water", type_code: str = "03", yearly: bool = False):
    return MeasurementFamily.objects.create(
        code=code, name="Счётчики воды", type_code=type_code, numbering_resets_yearly=yearly
    )


def make_employee(*, tab: str = "05", name: str = "Стариков А. А."):
    return Employee.objects.create(tab_number=tab, full_name=name)


def attest(employee: Employee, family: MeasurementFamily, *, years: int = 5):
    today = timezone.localdate()
    attestation = Attestation.objects.create(
        employee=employee,
        document="ААА 0001",
        valid_from=today - dt.timedelta(days=365),
        valid_to=today + dt.timedelta(days=365 * years),
    )
    attestation.families.add(family)
    return attestation


def make_instrument(family: MeasurementFamily, serial: str):
    si_type, _ = SiType.objects.get_or_create(
        family=family, registry_number="12345-06", defaults={"name": "СВК-15"}
    )
    return Instrument.objects.create(si_type=si_type, serial_number=serial)


def make_verification(
    family: MeasurementFamily, employee: Employee, *, day: int, serial: str | None = None
) -> Verification:
    """Поверка в указанный день марта 2026, 10:00 по местному времени."""
    moment = timezone.make_aware(dt.datetime(2026, 3, day, 10, 0))
    return Verification.objects.create(
        instrument=make_instrument(family, serial or f"SN{day:03d}"),
        verifier=employee,
        verified_at=moment,
    )


def make_protocol(verification: Verification) -> Protocol:
    return Protocol.objects.create(
        verification=verification,
        scope=scope_for(verification),
        status=ProtocolStatus.DRAFT,
    )


def draft(family, employee, *, day: int, serial: str | None = None) -> Protocol:
    return make_protocol(make_verification(family, employee, day=day, serial=serial))
