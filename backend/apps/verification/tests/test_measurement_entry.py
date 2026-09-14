"""Ввод измерений: с экрана поверителя и со скана бланка."""

from __future__ import annotations

import pytest
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import SiType
from apps.core.models import User
from apps.verification import measurements as service
from apps.verification.calculators import water_meter as wm
from apps.verification.models import VerificationStatus
from apps.verification.numbering import assign_numbers, scope_for, seal
from apps.verification.tests import factories as f

LIMITS = {
    "q_min": "0.03", "q_transition_a": "0.15", "q_transition_b": "0.12",
    "q_nominal": "1.5", "q_max": "3",
    "error_below_transition": "5", "error_above_transition": "2",
}

# Строки, дающие погрешность в допуске: δ = −4.8 %, −1.5 %, −1.0 %.
# Объём по счётчику — из показаний, объём по эталону — с установки.
PASSING = [
    {"flow_rate": "0.03", "reading_start": "229.0830", "reading_end": "229.0890",
     "volume_standard": "0.0063"},
    {"flow_rate": "0.125", "reading_start": "229.0960", "reading_end": "229.1090",
     "volume_standard": "0.0132"},
    {"flow_rate": "0.93", "reading_start": "229.1260", "reading_end": "229.1570",
     "volume_standard": "0.0313"},
]


class EntryTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)
        self.verification = f.make_verification(self.family, self.employee, day=6)
        SiType.objects.filter(pk=self.verification.instrument.si_type_id).update(limits=LIMITS)
        self.verification.instrument.si_type.refresh_from_db()

    def payload(self, **overrides):
        return {"layout": wm.LAYOUT_COMPACT, "meter_class": wm.CLASS_B,
                "rows": [dict(row) for row in PASSING]} | overrides

    # -- расчёт ------------------------------------------------------------
    def test_verdict_follows_from_the_numbers(self):
        applied = service.apply(self.verification, self.payload())

        assert applied.suitable
        assert [row["error_pct"] for row in applied.rows] == ["-4.8", "-1.5", "-1.0"]
        assert applied.rows[0]["limit_pct"] == "5"   # ниже переходного расхода
        assert applied.rows[1]["limit_pct"] == "2"   # от переходного и выше

    def test_out_of_tolerance_row_makes_it_unsuitable_with_a_reason(self):
        rows = [dict(row) for row in PASSING]
        rows[2]["volume_standard"] = "0.0280"        # счётчик намерил сильно больше
        applied = service.apply(self.verification, self.payload(rows=rows))

        assert not applied.suitable
        assert applied.verdict.failed_rows == (3,)
        self.verification.refresh_from_db()
        assert not self.verification.suitable
        assert "δ" in self.verification.unsuitability_reason

    def test_failed_tightness_check_overrides_good_numbers(self):
        applied = service.apply(
            self.verification, self.payload(checks={"tightness": False})
        )
        assert not applied.suitable
        assert "герметичности" in applied.verdict.reasons[0]

    def test_standard_volume_is_required(self):
        rows = [dict(row) for row in PASSING]
        rows[0].pop("volume_standard")
        with pytest.raises(service.MeasurementInputError, match="объём по эталону"):
            service.apply(self.verification, self.payload(rows=rows))

    def test_journal_note_is_ready_for_the_journal(self):
        applied = service.apply(self.verification, self.payload())
        assert applied.journal_note == "Поверен в диапазоне расхода (0,03-0.93) м3/ч"

    def test_results_are_stored_on_the_verification(self):
        service.apply(self.verification, self.payload())
        self.verification.refresh_from_db()

        assert self.verification.measurements["layout"] == wm.LAYOUT_COMPACT
        assert len(self.verification.results["rows"]) == 3
        assert self.verification.status == VerificationStatus.READY

    # -- проверки ввода ----------------------------------------------------
    def test_row_count_must_match_the_layout(self):
        with pytest.raises(service.MeasurementInputError, match="9 строк"):
            service.apply(self.verification, self.payload(layout=wm.LAYOUT_EXTENDED))

    def test_readings_going_backwards_are_rejected(self):
        rows = [dict(row) for row in PASSING]
        rows[0]["reading_end"] = "229.0000"
        with pytest.raises(service.MeasurementInputError, match="меньше, чем в начале"):
            service.apply(self.verification, self.payload(rows=rows))

    def test_comma_decimals_are_accepted(self):
        rows = [dict(row) for row in PASSING]
        rows[0]["flow_rate"] = "0,03"
        applied = service.apply(self.verification, self.payload(rows=rows))
        assert applied.rows[0]["flow_rate"] == "0.03"

    def test_missing_limits_in_the_catalogue_say_what_to_fill(self):
        SiType.objects.filter(pk=self.verification.instrument.si_type_id).update(limits={})
        self.verification.instrument.si_type.refresh_from_db()
        with pytest.raises(service.MeasurementInputError, match="не заполнены характеристики"):
            service.apply(self.verification, self.payload())

    def test_signed_protocol_cannot_be_re_measured(self):
        protocol = f.make_protocol(self.verification)
        assign_numbers(scope_for(self.verification))
        protocol.refresh_from_db()
        seal(protocol, signed_by=self.employee)
        self.verification.refresh_from_db()

        with pytest.raises(service.MeasurementInputError, match="уже подписан"):
            service.apply(self.verification, self.payload())

    def test_typo_in_the_standard_volume_goes_to_review(self):
        """Лишний ноль в объёме по эталону — расчётный Q × t не сходится."""
        rows = [dict(row) for row in PASSING]
        rows[0]["volume_standard"] = "0.063"
        applied = service.apply(self.verification, self.payload(rows=rows))

        assert applied.needs_review == [1]
        self.verification.refresh_from_db()
        assert self.verification.status == VerificationStatus.DRAFT

    def test_pulse_counter_needs_its_coefficient(self):
        rows = [{"flow_rate": "0.03", "pulses": 9, "volume_standard": "0.0063"}
                for _ in range(3)]
        with pytest.raises(service.MeasurementInputError, match="коэффициент"):
            service.apply(self.verification, self.payload(rows=rows))


class ScanTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)
        self.verification = f.make_verification(self.family, self.employee, day=6)
        SiType.objects.filter(pk=self.verification.instrument.si_type_id).update(limits=LIMITS)
        self.verification.instrument.si_type.refresh_from_db()

    def scan_payload(self, confidences):
        rows = [dict(row) | {"confidence": c} for row, c in zip(PASSING, confidences)]
        return {"layout": wm.LAYOUT_COMPACT, "source": service.SOURCE_SCAN, "rows": rows}

    def test_low_confidence_rows_go_to_review_and_block_normocontrol(self):
        applied = service.apply(self.verification, self.scan_payload([0.99, 0.62, 0.97]))

        assert applied.needs_review == [2]
        self.verification.refresh_from_db()
        assert self.verification.status == VerificationStatus.DRAFT

    def test_scan_without_confidence_is_always_reviewed(self):
        """Распознавание без оценки уверенности принимать молча нельзя."""
        applied = service.apply(self.verification, self.scan_payload([None, None, None]))
        assert applied.needs_review == [1, 2, 3]

    def test_confident_scan_still_computes_the_verdict(self):
        applied = service.apply(self.verification, self.scan_payload([0.99, 0.98, 0.97]))
        assert applied.needs_review == []
        assert applied.suitable
        self.verification.refresh_from_db()
        assert self.verification.status == VerificationStatus.READY

    def test_confirming_rows_clears_review_and_opens_normocontrol(self):
        service.apply(self.verification, self.scan_payload([0.99, 0.62, 0.55]))

        remaining = service.confirm_rows(self.verification, [2])
        assert remaining == [3]
        self.verification.refresh_from_db()
        assert self.verification.status == VerificationStatus.DRAFT

        remaining = service.confirm_rows(self.verification, [3])
        assert remaining == []
        self.verification.refresh_from_db()
        assert self.verification.status == VerificationStatus.READY


class ApiTestCase(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("p@ei.test", "pw"))
        self.family = f.make_family()
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)
        self.verification = f.make_verification(self.family, self.employee, day=6)
        SiType.objects.filter(pk=self.verification.instrument.si_type_id).update(limits=LIMITS)

    def test_post_measurements_returns_the_verdict(self):
        url = reverse("verification_measurements", args=[self.verification.pk])
        response = self.client.post(
            url, {"layout": wm.LAYOUT_COMPACT, "rows": PASSING}, format="json"
        )

        assert response.status_code == 200, response.data
        assert response.data["suitable"] is True
        assert response.data["needs_review"] == []
        assert len(response.data["rows"]) == 3

    def test_bad_input_comes_back_as_400_with_a_readable_message(self):
        url = reverse("verification_measurements", args=[self.verification.pk])
        rows = [dict(row) for row in PASSING]
        rows[0]["reading_end"] = "1.0"
        response = self.client.post(url, {"rows": rows}, format="json")

        assert response.status_code == 400
        assert "меньше, чем в начале" in response.data["detail"]

    def test_layouts_endpoint_describes_the_form(self):
        response = self.client.get(reverse("measurement_layouts"))

        assert response.status_code == 200
        assert len(response.data["layouts"][wm.LAYOUT_EXTENDED]) == 9
        assert response.data["layouts"][wm.LAYOUT_COMPACT][0]["seconds"] == 720
        assert "tightness" in response.data["checks"]
