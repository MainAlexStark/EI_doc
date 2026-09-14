"""Золотые тесты калькулятора счётчиков воды.

Эталон — пять реально выпущенных протоколов (ЕИ-03-02-00763…00767, август 2026),
обе раскладки шаблона: короткая на 3 строки измерений и полная на 9.
Значения вынуты из `.xlsm` такими, какими их посчитал Excel.

Проверяется, что Python считает **те же числа**: объём по эталону, показания в
конце измерения и относительная погрешность. Расходы и объёмы по счётчику —
входные данные, они берутся из протокола как есть.

Пересобрать эталон из новых файлов: `python tools/extract_golden.py <папка>`.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.test import SimpleTestCase

from apps.verification.calculators import water_meter as wm

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "water_meter_protocols.json").read_text("utf-8")
)

# Excel хранит результат в double, поэтому 0.014 лежит как 0.013999999999999999.
TOLERANCE = Decimal("5E-10")

# Характеристики из справочника типов шаблона (лист «Данные», K54:W98).
LIMITS = wm.MeterLimits.from_dict(
    {
        "q_min": "0.03", "q_transition_a": "0.15", "q_transition_b": "0.12",
        "q_nominal": "1.5", "q_max": "3",
        "error_below_transition": "5", "error_above_transition": "2",
    }
)


def cases():
    for protocol in GOLDEN:
        for row in protocol["measurements"]:
            yield protocol["protocol"], protocol["layout"], row


class GoldenTestCase(SimpleTestCase):
    def test_fixture_covers_both_layouts(self):
        layouts = {p["layout"] for p in GOLDEN}
        assert layouts == {"compact3", "extended9"}
        assert sum(len(p["measurements"]) for p in GOLDEN) == 33

    def test_volume_by_standard_matches_excel(self):
        """V эталона = ROUNDUP(Q × t / 3600, 3)."""
        mismatches = []
        for name, _layout, row in cases():
            measurement = wm.Measurement(
                flow_rate=Decimal(str(row["q"])),
                seconds=row["seconds"],
                volume_meter=Decimal(str(row["v_meter"])),
            )
            expected = Decimal(str(row["v_standard"]))
            if abs(measurement.volume_standard - expected) > TOLERANCE:
                mismatches.append(
                    f"{name} строка {row['row']}: получили {measurement.volume_standard}, "
                    f"в протоколе {expected}"
                )
        assert not mismatches, "\n".join(mismatches)

    def test_relative_error_matches_excel(self):
        """δ = ROUND((Vэт − Vсч) / Vсч × 100, 1)."""
        mismatches = []
        for name, _layout, row in cases():
            measurement = wm.Measurement(
                flow_rate=Decimal(str(row["q"])),
                seconds=row["seconds"],
                volume_meter=Decimal(str(row["v_meter"])),
            )
            expected = Decimal(str(row["error_pct"]))
            if measurement.relative_error != expected:
                mismatches.append(
                    f"{name} строка {row['row']}: получили {measurement.relative_error} %, "
                    f"в протоколе {expected} %"
                )
        assert not mismatches, "\n".join(mismatches)

    def test_reading_end_matches_excel(self):
        """Показания в конце = показания в начале + V эталона."""
        mismatches = []
        for name, _layout, row in cases():
            measurement = wm.Measurement(
                flow_rate=Decimal(str(row["q"])),
                seconds=row["seconds"],
                volume_meter=Decimal(str(row["v_meter"])),
                reading_start=Decimal(str(row["reading_start"])),
            )
            expected = Decimal(str(row["reading_end"]))
            if abs(measurement.reading_end - expected) > TOLERANCE:
                mismatches.append(
                    f"{name} строка {row['row']}: получили {measurement.reading_end}, "
                    f"в протоколе {expected}"
                )
        assert not mismatches, "\n".join(mismatches)

    def test_all_golden_protocols_are_suitable(self):
        """Все пять протоколов выпущены как годные — калькулятор должен согласиться."""
        for protocol in GOLDEN:
            measurements = [
                wm.Measurement(
                    flow_rate=Decimal(str(row["q"])),
                    seconds=row["seconds"],
                    volume_meter=Decimal(str(row["v_meter"])),
                )
                for row in protocol["measurements"]
            ]
            verdict = wm.evaluate(measurements, LIMITS, protocol["meter_class"])
            assert verdict.suitable, f"{protocol['protocol']}: {verdict.reasons}"


class RoundingTestCase(SimpleTestCase):
    def test_excel_round_is_half_away_from_zero(self):
        """Банковское округление Python дало бы 0.2 и −0.2."""
        assert wm.excel_round("0.25", 1) == Decimal("0.3")
        assert wm.excel_round("-0.25", 1) == Decimal("-0.3")

    def test_excel_roundup_always_goes_away_from_zero(self):
        assert wm.excel_roundup("0.0201", 3) == Decimal("0.021")
        assert wm.excel_roundup("0.020", 3) == Decimal("0.020")
        assert wm.excel_roundup("-0.0201", 3) == Decimal("-0.021")


class LimitsTestCase(SimpleTestCase):
    def test_transition_boundary_belongs_to_the_stricter_range(self):
        """В протоколе напечатано «от 0,12 м3/ч до 3 м3/ч : ± 2 %»."""
        assert LIMITS.error_limit("0.119", wm.CLASS_B) == Decimal("5")
        assert LIMITS.error_limit("0.12", wm.CLASS_B) == Decimal("2")

    def test_class_a_starts_at_double_the_minimum(self):
        assert LIMITS.range_start(wm.CLASS_A) == Decimal("0.06")
        assert LIMITS.range_start(wm.CLASS_B) == Decimal("0.03")
        assert LIMITS.error_limit("0.14", wm.CLASS_A) == Decimal("5")
        assert LIMITS.error_limit("0.15", wm.CLASS_A) == Decimal("2")

    def test_unknown_class_is_rejected(self):
        with pytest.raises(ValueError, match="Класс счётчика"):
            LIMITS.error_limit("0.1", "C")


class VerdictTestCase(SimpleTestCase):
    def make(self, flow_rate, volume_meter):
        return wm.Measurement(
            flow_rate=Decimal(flow_rate), seconds=720, volume_meter=Decimal(volume_meter)
        )

    def test_out_of_tolerance_row_makes_the_meter_unsuitable(self):
        good = self.make("0.03", "0.0063")     # δ = −4.8 % при допуске 5 %
        bad = self.make("0.03", "0.0068")      # δ = −11.8 %
        verdict = wm.evaluate([good, bad], LIMITS, wm.CLASS_B)

        assert not verdict.suitable
        assert verdict.failed_rows == (2,)
        assert "δ = -11.8 %" in verdict.reasons[0]

    def test_flow_range_note_matches_the_journal_wording(self):
        measurements = [self.make("0.03", "0.0063"), self.make("0.948", "0.19")]
        note = wm.flow_range_note(measurements, LIMITS)
        assert note == "Поверен в диапазоне расхода (0,03-0.948) м3/ч"

    def test_zero_meter_volume_is_an_error_not_a_crash_later(self):
        with pytest.raises(ZeroDivisionError, match="Объём по счётчику"):
            self.make("0.03", "0").relative_error
