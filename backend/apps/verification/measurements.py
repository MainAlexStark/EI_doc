"""Ввод измерений в поверку — с экрана и со скана бланка.

Оба входа сходятся здесь, поэтому расчёт и проверки одни и те же независимо
от того, набрал поверитель цифры на телефоне или их распознали с фотографии.

Что делает сервис:

* собирает строки измерений по раскладке протокола (3 или 9 проливов);
* считает объём по эталону, погрешность и предел допуска;
* выводит годность из чисел и отметок по осмотру — отдельной «галочки
  годности» нет, вердикт следует из данных;
* складывает введённое в ``Verification.measurements``, посчитанное —
  в ``Verification.results``.

Для скана каждая строка несёт уверенность распознавания: всё ниже порога
помечается ``needs_review`` и требует подтверждения человеком, а поверка
остаётся черновиком, пока не подтверждена хотя бы одна спорная строка.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.verification.calculators import water_meter as wm
from apps.verification.models import Verification, VerificationStatus

SOURCE_MANUAL = "manual"
SOURCE_SCAN = "scan"
SOURCES = (SOURCE_MANUAL, SOURCE_SCAN)

# Ниже этого порога распознанное значение показывается человеку на сверку.
REVIEW_CONFIDENCE = 0.90

# Насколько объём по эталону может разойтись с расчётным Q × t, прежде чем
# строку покажут человеку. Установка держит расход неточно, поэтому проценты,
# а не доли; но лишний ноль в цифре так уже не проедет.
MAX_STANDARD_DEVIATION_PCT = 25


class MeasurementInputError(ValueError):
    """Ошибка ввода — показывается поверителю, а не падает пятисоткой."""


@dataclass
class Applied:
    verification: Verification
    verdict: wm.Verdict
    rows: list[dict]
    needs_review: list[int]
    journal_note: str

    @property
    def suitable(self) -> bool:
        return self.verdict.suitable


def _decimal(value, field: str, row: int | None = None) -> Decimal:
    where = f" (строка {row})" if row else ""
    if value is None or value == "":
        raise MeasurementInputError(f"Не заполнено поле «{field}»{where}")
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise MeasurementInputError(f"«{field}»{where}: не число — {value!r}") from exc


def limits_for(verification: Verification) -> wm.MeterLimits:
    """Метрологические характеристики типа СИ из справочника."""
    raw = verification.instrument.si_type.limits or {}
    missing = [
        key for key in (
            "q_min", "q_transition_a", "q_transition_b",
            "q_nominal", "q_max", "error_below_transition", "error_above_transition",
        )
        if key not in raw
    ]
    if missing:
        raise MeasurementInputError(
            f"У типа «{verification.instrument.si_type}» не заполнены характеристики: "
            f"{', '.join(missing)}. Дозаполните справочник типов СИ."
        )
    return wm.MeterLimits.from_dict(raw)


def build_rows(payload: dict) -> tuple[list[wm.Measurement], list[dict], list[int]]:
    """Разобрать вход в строки измерений.

    Возвращает (строки для расчёта, строки как их ввели, номера спорных строк).
    """
    layout = payload.get("layout") or wm.LAYOUT_COMPACT
    if layout not in wm.LAYOUTS:
        raise MeasurementInputError(
            f"Неизвестная раскладка протокола {layout!r}. Ожидается одна из: "
            f"{', '.join(wm.LAYOUTS)}"
        )

    expected_modes = wm.LAYOUTS[layout]
    raw_rows = payload.get("rows") or []
    if len(raw_rows) != len(expected_modes):
        raise MeasurementInputError(
            f"Раскладка «{layout}» — это {len(expected_modes)} строк измерений, "
            f"пришло {len(raw_rows)}"
        )

    source = payload.get("source", SOURCE_MANUAL)
    if source not in SOURCES:
        raise MeasurementInputError(f"Неизвестный источник данных {source!r}")

    pulse_weight = payload.get("pulse_weight")
    pulse_weight = _decimal(pulse_weight, "коэффициент преобразования") if pulse_weight else None

    measurements: list[wm.Measurement] = []
    stored: list[dict] = []
    needs_review: list[int] = []

    for index, (mode, raw) in enumerate(zip(expected_modes, raw_rows), start=1):
        seconds = raw.get("seconds") or wm.MODES[mode]["seconds"]
        kwargs = {
            "flow_rate": _decimal(raw.get("flow_rate"), "расход", index),
            "seconds": int(seconds),
            "mode": mode,
            # Объём по эталону показывает установка — вывести его из расхода
            # нельзя, иначе погрешность всегда окажется нулевой.
            "volume_standard": _decimal(raw.get("volume_standard"), "объём по эталону", index),
        }

        if raw.get("pulses") not in (None, ""):
            kwargs["pulses"] = int(raw["pulses"])
            kwargs["pulse_weight"] = pulse_weight
        elif raw.get("volume_meter") not in (None, ""):
            kwargs["volume_meter"] = _decimal(raw["volume_meter"], "объём по счётчику", index)
        else:
            kwargs["reading_start"] = _decimal(raw.get("reading_start"), "показания в начале", index)
            kwargs["reading_end"] = _decimal(raw.get("reading_end"), "показания в конце", index)

        try:
            measurement = wm.Measurement(**kwargs)
        except wm.MeasurementError as exc:
            raise MeasurementInputError(f"Строка {index}: {exc}") from exc

        confidence = raw.get("confidence")
        row_needs_review = bool(raw.get("needs_review"))
        if source == SOURCE_SCAN:
            if confidence is None or float(confidence) < REVIEW_CONFIDENCE:
                row_needs_review = True

        # Объём по эталону сильно разошёлся с расчётным Q × t — почти всегда
        # это опечатка в цифре или в длительности пролива.
        deviation = measurement.standard_deviation_pct
        if abs(deviation) > MAX_STANDARD_DEVIATION_PCT:
            row_needs_review = True

        if row_needs_review:
            needs_review.append(index)

        measurements.append(measurement)
        stored.append(
            {
                "row": index,
                "mode": mode,
                "seconds": int(seconds),
                "flow_rate": str(measurement.flow_rate),
                "volume_standard": str(measurement.volume_standard),
                "volume_meter": str(measurement.volume_meter),
                "deviation_pct": str(deviation),
                "reading_start": str(raw["reading_start"]) if raw.get("reading_start") not in (None, "") else None,
                "reading_end": str(raw["reading_end"]) if raw.get("reading_end") not in (None, "") else None,
                "pulses": raw.get("pulses"),
                "confidence": confidence,
                "needs_review": row_needs_review,
            }
        )

    return measurements, stored, needs_review


@transaction.atomic
def apply(verification: Verification, payload: dict) -> Applied:
    """Записать измерения в поверку и пересчитать результат."""
    if hasattr(verification, "protocol") and verification.protocol.is_sealed:
        raise MeasurementInputError(
            f"Протокол {verification.protocol.full_number} уже подписан — "
            "измерения меняются только выпуском новой версии"
        )

    limits = limits_for(verification)
    meter_class = payload.get("meter_class") or wm.CLASS_B
    if meter_class not in (wm.CLASS_A, wm.CLASS_B):
        raise MeasurementInputError(
            f"Класс счётчика должен быть {wm.CLASS_A!r} или {wm.CLASS_B!r}, пришло {meter_class!r}"
        )

    checks = {key: bool(payload.get("checks", {}).get(key, True)) for key in wm.CHECKS}
    measurements, stored, needs_review = build_rows(payload)

    try:
        verdict = wm.evaluate(measurements, limits, meter_class, checks=checks)
    except wm.MeasurementError as exc:
        raise MeasurementInputError(str(exc)) from exc

    rows = [m.as_result(limits, meter_class) for m in measurements]
    journal_note = wm.flow_range_note(measurements, limits)

    verification.measurements = {
        "layout": payload.get("layout") or wm.LAYOUT_COMPACT,
        "source": payload.get("source", SOURCE_MANUAL),
        "meter_class": meter_class,
        "pulse_weight": str(payload["pulse_weight"]) if payload.get("pulse_weight") else None,
        # Температура поверочной жидкости — отдельная строка в условиях поверки,
        # к строкам измерений отношения не имеет.
        "water_temperature": (
            str(payload["water_temperature"]) if payload.get("water_temperature") else None
        ),
        "checks": checks,
        "rows": stored,
    }
    verification.results = {
        "rows": rows,
        "suitable": verdict.suitable,
        "failed_rows": list(verdict.failed_rows),
        "max_flow_rate": str(verdict.max_flow_rate),
        "journal_note": journal_note,
        "reasons": list(verdict.reasons),
    }
    verification.suitable = verdict.suitable
    verification.unsuitability_reason = "; ".join(verdict.reasons) if not verdict.suitable else ""

    # Со сканом поверка не уходит на нормоконтроль, пока спорные строки
    # не подтверждены человеком.
    if needs_review:
        verification.status = VerificationStatus.DRAFT
    elif verification.status == VerificationStatus.DRAFT:
        verification.status = VerificationStatus.READY

    verification.save(
        update_fields=[
            "measurements", "results", "suitable",
            "unsuitability_reason", "status", "updated_at",
        ]
    )

    return Applied(
        verification=verification,
        verdict=verdict,
        rows=rows,
        needs_review=needs_review,
        journal_note=journal_note,
    )


def confirm_rows(verification: Verification, rows: list[int]) -> list[int]:
    """Подтвердить распознанные строки после сверки с фотографией.

    Возвращает оставшиеся спорные строки. Когда их не осталось, поверка
    уходит на нормоконтроль.
    """
    data = verification.measurements or {}
    stored = data.get("rows") or []
    if not stored:
        raise MeasurementInputError("В поверке нет измерений")

    to_confirm = set(rows)
    for row in stored:
        if row["row"] in to_confirm:
            row["needs_review"] = False
            row["confirmed"] = True

    remaining = [row["row"] for row in stored if row.get("needs_review")]
    verification.measurements = data
    if not remaining and verification.status == VerificationStatus.DRAFT:
        verification.status = VerificationStatus.READY
    verification.save(update_fields=["measurements", "status", "updated_at"])
    return remaining
