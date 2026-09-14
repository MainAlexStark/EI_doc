"""Журнал учёта поверочных работ.

Журнал — не таблица в базе, а представление над поверками. Здесь живёт
выборка для экрана и выгрузка в тот самый `.xlsx`, который метролог
отправляет в ФИФ: 49 колонок, заголовки один в один с прежним файлом.

Столбцы формата ФИФ ОЕИ заполняются ровно так, как это делалось вручную, —
в частности, адрес поверки идёт в графу «Владелец СИ», а собственник в графу
«Собственник». Это не ошибка, это их сложившееся соответствие.
"""

from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from pathlib import Path

from django.db.models import QuerySet

from apps.verification.models import Protocol, ProtocolStatus, Verification

COLUMNS_FIXTURE = Path(__file__).parent / "fixtures" / "journal_columns.json"
SHEET_NAME = "Пример"

# Куда какое поле ложится в 49-колоночном формате ФИФ.
COLUMN_MAP = {
    0: "protocol_number",
    1: "always_one",
    2: "si_registry_number",
    5: "si_name",
    7: "serial_number",
    9: "verified_at",
    10: "next_verification_date",
    12: "method",
    13: "suitability",
    14: "temperature",
    15: "pressure",
    16: "humidity",
    21: "standards",
    32: "address",
    34: "mark_on_si",
    35: "verifier_name",
    38: "unsuitability_reason",
    41: "other_info",
    44: "manufacture_year",
    45: "readings",
    47: "owner",
    48: "unit_type",
}


def columns() -> list[dict]:
    return json.loads(COLUMNS_FIXTURE.read_text(encoding="utf-8"))


def journal_queryset() -> QuerySet[Verification]:
    """Поверки со всем, что нужно журналу, без лишних запросов."""
    return (
        Verification.objects.select_related(
            "instrument__si_type", "instrument__owner", "instrument__site",
            "verifier", "method", "protocol__scope__series", "protocol__scope__employee",
        )
        .prefetch_related("standards")
        .order_by("-verified_at", "-id")
    )


def row_values(verification: Verification) -> dict[str, object]:
    """Значения строки журнала по именам полей из COLUMN_MAP."""
    instrument = verification.instrument
    si_type = instrument.si_type
    protocol = getattr(verification, "protocol", None)
    results = verification.results or {}
    entered = verification.measurements or {}

    readings = None
    rows = entered.get("rows") or []
    if rows and rows[0].get("reading_start"):
        readings = rows[0]["reading_start"]

    return {
        "protocol_number": protocol.full_number if protocol else "",
        "always_one": "1",
        "si_registry_number": si_type.registry_number,
        "si_name": si_type.name,
        "serial_number": instrument.serial_number,
        "verified_at": verification.verified_at.date(),
        "next_verification_date": verification.next_verification_date,
        "method": str(verification.method) if verification.method_id else "",
        "suitability": "Пригодно" if verification.suitable else "Непригодно",
        "temperature": f"{verification.temperature} °C" if verification.temperature is not None else "",
        "pressure": f"{verification.pressure} кП" if verification.pressure is not None else "",
        "humidity": f"{verification.humidity} %" if verification.humidity is not None else "",
        "standards": ", ".join(s.fif_number for s in verification.standards.all()),
        "address": str(instrument.site) if instrument.site_id else "",
        "mark_on_si": "1",
        "verifier_name": verification.verifier.full_name,
        "unsuitability_reason": verification.unsuitability_reason,
        "other_info": results.get("journal_note", ""),
        "manufacture_year": instrument.manufacture_year,
        "readings": readings,
        "owner": str(instrument.owner) if instrument.owner_id else "Частное лицо",
        "unit_type": entered.get("unit_type") or "",
    }


def export_xlsx(verifications: QuerySet[Verification] | list[Verification]) -> bytes:
    """Собрать журнал в `.xlsx` формата ФИФ ОЕИ.

    Файл строится с нуля: старый шаблон не открывается и не правится, поэтому
    ломаться нечему.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME

    header = columns()
    sheet.append([column["title"] for column in header])
    for cell in sheet[1]:
        cell.font = Font(bold=True, size=9)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[1].height = 60
    sheet.freeze_panes = "A2"

    for verification in verifications:
        values = row_values(verification)
        row = [None] * len(header)
        for index, field in COLUMN_MAP.items():
            row[index] = values.get(field)
        sheet.append(row)

    for index in range(len(header)):
        letter = sheet.cell(row=1, column=index + 1).column_letter
        sheet.column_dimensions[letter].width = 22 if index in COLUMN_MAP else 10

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def filename(start: date | None = None, end: date | None = None) -> str:
    if start and end:
        return f"Журнал учёта поверочных работ {start:%Y-%m-%d}—{end:%Y-%m-%d}.xlsx"
    return "Журнал учёта поверочных работ.xlsx"


def normocontrol_summary(scope) -> dict:
    """Сводка по области нумерации для экрана нормоконтроля."""
    from apps.verification import numbering

    protocols = Protocol.objects.filter(scope=scope)
    breaks = numbering.chronology_breaks(scope)
    return {
        "id": scope.pk,
        "title": str(scope),
        "series": scope.series.code,
        "employee": scope.employee.full_name,
        "year": scope.year,
        "sealed_high_water": numbering.sealed_high_water(scope),
        "drafts": protocols.filter(status=ProtocolStatus.DRAFT).count(),
        "numbered": protocols.filter(status=ProtocolStatus.NUMBERED).count(),
        "signed": protocols.filter(status=ProtocolStatus.SIGNED).count(),
        "published": protocols.filter(status=ProtocolStatus.PUBLISHED).count(),
        "chronology_breaks": [
            {
                "previous": previous.full_number,
                "current": current.full_number,
                "reason": current.out_of_sequence_reason,
            }
            for previous, current in breaks
        ],
    }
