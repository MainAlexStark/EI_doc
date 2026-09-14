"""Сборка данных протокола и рендер PDF.

Вёрстка шаблона повторяет прежний документ из `.xlsm` — тесты следят за тем,
чтобы в него попадали те же формулировки и то же форматирование чисел.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import SimpleTestCase, TestCase

from apps.catalog.models import SiType
from apps.verification import measurements as service
from apps.verification import render
from apps.verification.calculators import water_meter as wm
from apps.verification.tests import factories as f

LIMITS_DICT = {
    "q_min": "0.03", "q_transition_a": "0.15", "q_transition_b": "0.12",
    "q_nominal": "1.5", "q_max": "3",
    "error_below_transition": "5", "error_above_transition": "2",
}
LIMITS = wm.MeterLimits.from_dict(LIMITS_DICT)

ROWS = [
    {"flow_rate": "0.030", "reading_start": "1091.763", "reading_end": "1091.769",
     "volume_standard": "0.0063"},
    {"flow_rate": "0.132", "reading_start": "1091.775", "reading_end": "1091.789",
     "volume_standard": "0.0142"},
    {"flow_rate": "0.666", "reading_start": "1091.807", "reading_end": "1091.830",
     "volume_standard": "0.0226"},
]


class FormattingTestCase(SimpleTestCase):
    def test_numbers_use_a_comma_and_fixed_digits(self):
        """В протоколе расход всегда с тремя знаками, Vэтал — с четырьмя."""
        assert render.ru("0.03", 3) == "0,030"
        assert render.ru("0.0063", 4) == "0,0063"
        assert render.ru("-4.8", 1) == "-4,8"
        assert render.ru(None, 3) == ""

    def test_mode_labels_match_the_old_wording(self):
        labels = render.mode_labels(LIMITS)

        assert "(60 + 6) л/ч (класс А)" in labels[wm.MODE_MIN]
        assert "(30 + 3) л/ч (класс В)" in labels[wm.MODE_MIN]
        assert "(165 ± 16,5) л/ч (класс А)" in labels[wm.MODE_TRANSITION]
        assert "(132 ± 13,2) л/ч (класс В)" in labels[wm.MODE_TRANSITION]
        assert labels[wm.MODE_MAX] == "Измерения на расходе Qнаиб , л/ч"

    def test_ranges_block_matches_the_old_wording(self):
        lines = render.ranges_block(LIMITS)

        assert lines[0] == "от 0,06 м3/ч до 3 м3/ч  (класс А)"
        assert lines[1] == "от 0,03 м3/ч до 3 м3/ч  (класс В)"
        assert "от 0,12 м3/ч до 3 м3/ч : ± 2 %" in lines


class ContextTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)
        self.verification = f.make_verification(self.family, self.employee, day=7)
        self.verification.temperature = Decimal("21")
        self.verification.humidity = Decimal("53.1")
        self.verification.pressure = Decimal("100")
        self.verification.save()
        SiType.objects.filter(pk=self.verification.instrument.si_type_id).update(
            limits=LIMITS_DICT, name="СХВ-15", registry_number="16078-13"
        )
        self.verification.instrument.si_type.refresh_from_db()
        service.apply(
            self.verification,
            {"layout": wm.LAYOUT_COMPACT, "meter_class": wm.CLASS_B,
             "pulse_weight": "0.00817", "rows": ROWS},
        )
        self.verification.refresh_from_db()

    def context(self):
        return render.build_context(self.verification, number="ЕИ-03-02-00767")

    def test_table_rows_carry_both_volumes_and_the_readings(self):
        row = self.context()["rows"][0]

        assert row["flow_rate"] == "0,030"
        assert row["reading_start"] == "1091,763"
        assert row["reading_end"] == "1091,769"
        assert row["volume_meter"] == "0,006"      # графа «Vсчет»
        assert row["volume_standard"] == "0,0063"  # графа «Vэтал»
        assert row["error_pct"] == "-4,8"
        assert row["limit_pct"] == "5"

    def test_subject_line_matches_the_old_wording(self):
        subject = self.context()["subject"]
        assert subject.startswith("периодической поверки СИ - счетчик воды СХВ-15, год изготовления")

    def test_conclusion_follows_the_verdict(self):
        assert "пригодно к применению" in self.context()["conclusion"]

        self.verification.suitable = False
        self.verification.save(update_fields=["suitable"])
        assert "не пригодно к применению" in self.context()["conclusion"]

    def test_pulse_weight_is_printed_with_a_comma(self):
        assert self.context()["pulse_weight"] == "0,00817"

    def test_verification_without_results_is_not_printable(self):
        self.verification.results = {}
        self.verification.save(update_fields=["results"])
        with pytest.raises(render.RenderError, match="печатать нечего"):
            self.context()

    @pytest.mark.skipif(not render.typst_available(), reason="typst не установлен")
    def test_pdf_actually_compiles(self):
        pdf = render.render_pdf(self.context())

        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 10_000
