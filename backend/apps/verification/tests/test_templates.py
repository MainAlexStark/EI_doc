"""Поверка по шаблону: шаблон → данные поверителя → расчёт, годность, протокол."""

from __future__ import annotations

import copy

import pytest
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Role, User
from apps.verification import numbering, render, templates
from apps.verification.models import VerificationTemplate
from apps.verification.tests import factories as f

MANOMETER = {
    "si_kind": "Манометры показывающие",
    "method": "МИ 2124-90 «ГСИ. Манометры… показывающие и самопишущие. Методика поверки»",
    "quantity": "давление", "unit": "МПа",
    "params": [
        {"key": "upper", "label": "Верхний предел измерений", "unit": "МПа", "from_type": "upper", "sample": "1.6"},
        {"key": "cls", "label": "Класс точности", "from_type": "accuracy_class", "sample": "1.5"},
    ],
    "operations": [
        {"key": "visual", "label": "Внешний осмотр", "source": "МИ 2124-90, п. 5.1"},
        {"key": "testing", "label": "Опробование", "source": "МИ 2124-90, п. 5.2"},
    ],
    "points": [{"key": "up", "label": "Прямой ход"}, {"key": "down", "label": "Обратный ход"}],
    "repeats": 1,
    "modes": [
        {"key": "p0", "setpoint": "0", "label": "{setpoint} {unit}"},
        {"key": "p50", "setpoint": "upper * 0.5", "label": "{setpoint} {unit}"},
        {"key": "p100", "setpoint": "upper", "label": "{setpoint} {unit}"},
    ],
    "characteristics": [
        {"key": "err_up", "label": "Основная погрешность, прямой ход", "expr": 'mean(point("up")) - setpoint',
         "text": "Δ = Pи − Pэ", "decimals": 3,
         "tolerance": {"kind": "abs", "limit": "cls / 100 * upper"}, "source": "МИ 2124-90, п. 5.3"},
        {"key": "err_down", "label": "Основная погрешность, обратный ход", "expr": 'mean(point("down")) - setpoint',
         "text": "Δ = Pи − Pэ", "decimals": 3,
         "tolerance": {"kind": "abs", "limit": "cls / 100 * upper"}, "source": "МИ 2124-90, п. 5.3"},
        {"key": "variation", "label": "Вариация показаний", "expr": 'abs(mean(point("up")) - mean(point("down")))',
         "text": "|Pпр − Pобр|", "decimals": 3,
         "tolerance": {"kind": "abs", "limit": "cls / 100 * upper"}, "source": "МИ 2124-90, п. 5.4"},
    ],
}

GOOD = {
    "p0": {"readings": {"up": ["0"], "down": ["0"]}},
    "p50": {"readings": {"up": ["0.81"], "down": ["0.80"]}},
    "p100": {"readings": {"up": ["1.60"], "down": ["1.61"]}},
}


class TemplateCleanTests(TestCase):
    def test_valid_template_is_canonical(self):
        plan = templates.clean_template(copy.deepcopy(MANOMETER))
        assert plan["modes"][1]["setpoint"] == "upper * 0.5"
        assert [op["key"] for op in plan["operations"]] == ["visual", "testing"]

    def test_resolve_numbers_points_and_labels(self):
        plan = templates.resolve(templates.clean_template(copy.deepcopy(MANOMETER)), {"upper": 2.5, "cls": 1})
        assert [m["setpoint"] for m in plan["modes"]] == ["0", "1.25", "2.5"]
        assert plan["modes"][1]["label"] == "1,25 МПа"
        assert plan["flavor"] == "verification"

    def test_no_measurements_or_instrument_in_template(self):
        for key in ("readings", "measurements", "serial_number"):
            with pytest.raises(templates.TemplateError, match="не может быть"):
                templates.clean_template({**copy.deepcopy(MANOMETER), key: {}})

    def test_unknown_names_and_missing_sources(self):
        bad = copy.deepcopy(MANOMETER)
        bad["modes"][1]["setpoint"] = "top * 0.5"
        with pytest.raises(templates.TemplateError, match="top"):
            templates.clean_template(bad)
        bad = copy.deepcopy(MANOMETER)
        bad["characteristics"][0]["source"] = ""
        with pytest.raises(templates.TemplateError, match="источник"):
            templates.clean_template(bad)

    def test_preview_renders_blank_protocol(self):
        from apps.assistant import documents

        spec = templates.preview_spec(templates.clean_template(copy.deepcopy(MANOMETER)), title="Манометры")
        assert spec["title"] == "Протокол периодической поверки"
        text = str(spec["blocks"])
        assert "Внешний осмотр: ________" in text and "Основная погрешность" in text
        assert documents.render_docx(spec)[:2] == b"PK"


class TemplateVerificationTests(TestCase):
    def setUp(self):
        self.family = f.make_family(code="manometers", name="Манометры")
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)
        self.si_type = f.make_si_type(self.family, registry_number="39153-08", name="ДМ",
                                      limits={"upper": 1.6, "accuracy_class": 1.5})
        self.verification = f.make_verification(self.family, self.employee, day=6, si_type=self.si_type)
        self.metrologist = User.objects.create_user("m@ei.test", "pw", role=Role.METROLOGIST)
        self.verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.template = templates.save_template(
            self.verifier, {"title": "Манометры — МИ 2124-90", "family": self.family.pk,
                            "plan": copy.deepcopy(MANOMETER)}, sources=["МИ 2124-90"],
        )
        self.client = APIClient()
        self.client.force_authenticate(self.verifier)

    def post(self, **over):
        data = {"operations": {"visual": True, "testing": True}, "readings": copy.deepcopy(GOOD), **over}
        return self.client.post(reverse("verification_measurements", args=[self.verification.pk]), data, format="json")

    def test_template_from_verifier_waits_for_review(self):
        assert self.template.needs_review and self.template.created_by == self.verifier

    def test_form_info_has_params_from_type_and_numeric_points(self):
        r = self.client.get(reverse("verification_template_for", args=[self.verification.pk]))
        assert r.status_code == 200, r.data
        assert r.data["template"]["id"] == self.template.pk and r.data["missing"] == []
        assert [m["setpoint"] for m in r.data["modes"]] == ["0", "0,8", "1,6"]
        assert r.data["params_from_type"] == ["cls", "upper"]

    def test_suitable_then_protocol_docx(self):
        r = self.post()
        assert r.status_code == 200, r.data
        assert r.data["suitable"] is True and r.data["status"] == "ready"
        self.verification.refresh_from_db()
        assert self.verification.measurements["engine"] == "template"
        assert self.verification.measurements["plan"]["modes"][2]["setpoint"] == "1.6"
        r = self.client.get(reverse("verification_protocol", args=[self.verification.pk, "docx"]))
        assert r.status_code == 200, getattr(r, "data", None)
        assert r.content[:2] == b"PK"
        spec = templates.protocol_spec(self.verification)
        text = str(spec["blocks"])
        assert "39153-08" in text and "соответствует" in text and spec["draft"] is True
        assert "признано пригодным" in text

    def test_error_out_of_tolerance_and_failed_operation(self):
        readings = copy.deepcopy(GOOD)
        readings["p50"]["readings"]["up"] = ["0.85"]  # 0,05 при допуске ±0,024
        r = self.post(readings=readings)
        assert r.status_code == 200 and r.data["suitable"] is False
        assert any("прямой ход" in x for x in r.data["reasons"])
        r = self.post(operations={"visual": False, "testing": True})
        assert r.data["suitable"] is False and "Внешний осмотр — не соответствует" in r.data["reasons"]
        self.verification.refresh_from_db()
        assert "непригодным" in str(templates.protocol_spec(self.verification)["blocks"])

    def test_incomplete_and_unmarked_are_400(self):
        readings = copy.deepcopy(GOOD)
        readings["p100"]["readings"]["down"] = [""]
        assert "Не хватает показаний" in self.post(readings=readings).data["detail"]
        assert "Отметьте операции" in self.post(operations={"visual": True}).data["detail"]

    def test_manual_unsuitable_without_readings(self):
        r = self.post(readings={}, operations={}, manual_unsuitable=True, manual_unsuitability_reason="Разбито стекло")
        assert r.status_code == 200, r.data
        assert r.data["suitable"] is False and "Разбито стекло" in r.data["reasons"]
        self.verification.refresh_from_db()
        assert "Разбито стекло" in str(templates.protocol_spec(self.verification)["blocks"])

    def test_param_entered_by_verifier_is_remembered(self):
        plan = copy.deepcopy(MANOMETER)
        plan["params"].append({"key": "div", "label": "Цена деления", "sample": "0.05"})
        templates.save_template(self.metrologist, {"title": "ДМ — свой", "family": self.family.pk,
                                                   "si_type": self.si_type.pk, "plan": plan},
                                template_id=None)
        r = self.post()
        assert r.status_code == 400 and "Цена деления" in r.data["detail"]
        r = self.post(params={"div": "0,05"})
        assert r.status_code == 200, r.data
        self.verification.instrument.refresh_from_db()
        assert self.verification.instrument.attributes["div"] == "0.05"

    def test_seal_refused_until_template_reviewed(self):
        self.post()
        protocol = f.make_protocol(self.verification)
        protocol.seq, protocol.status = 1, "numbered"
        protocol.save()
        with pytest.raises(numbering.NumberingError, match="не проверен метрологом"):
            numbering.seal(protocol, signed_by=self.employee)
        self.client.force_authenticate(self.verifier)
        assert self.client.post(reverse("verification_template_approve", args=[self.template.pk])).status_code == 403
        self.client.force_authenticate(self.metrologist)
        assert self.client.post(reverse("verification_template_approve", args=[self.template.pk])).status_code == 200
        numbering.seal(protocol, signed_by=self.employee)
        protocol.refresh_from_db()
        assert protocol.status == "signed"
        self.verification.refresh_from_db()
        assert templates.protocol_spec(self.verification)["draft"] is False

    def test_template_change_does_not_touch_done_verification(self):
        self.post()
        plan = copy.deepcopy(MANOMETER)
        plan["modes"] = plan["modes"][:2]
        templates.save_template(self.metrologist, {"title": self.template.title, "family": self.family.pk,
                                                   "plan": plan}, template_id=self.template.pk)
        self.verification.refresh_from_db()
        assert len(self.verification.measurements["plan"]["modes"]) == 3

    def test_list_and_preview_endpoints(self):
        r = self.client.get(reverse("verification_templates"))
        assert r.status_code == 200 and r.data[0]["plan"]["params"][0]["key"] == "upper"
        r = self.client.get(reverse("verification_template_preview", args=[self.template.pk, "docx"]))
        assert r.status_code == 200 and r.content[:2] == b"PK"

    @pytest.mark.skipif(not render.typst_available(), reason="нет typst")
    def test_protocol_pdf(self):
        self.post()
        r = self.client.get(reverse("verification_protocol", args=[self.verification.pk, "pdf"]))
        assert r.status_code == 200 and r.content[:4] == b"%PDF"


class WaterMeterStaysOnCalculatorTests(TestCase):
    def test_water_family_ignores_template(self):
        from apps.catalog.api_types import WATER_CALCULATOR

        family = f.make_family()
        family.calculator_key = WATER_CALCULATOR
        family.save()
        employee = f.make_employee()
        verification = f.make_verification(family, employee, day=6)
        user = User.objects.create_user("x@ei.test", "pw", role=Role.METROLOGIST)
        templates.save_template(user, {"title": "вода", "family": family.pk, "plan": copy.deepcopy(MANOMETER)})
        assert templates.uses_template(verification) is False


class AssistantVerificationTemplateTests(TestCase):
    def setUp(self):
        from apps.assistant.models import Conversation
        from apps.assistant.tools import ToolContext

        self.family = f.make_family(code="manometers", name="Манометры")
        self.user = User.objects.create_user("v2@ei.test", "pw", role=Role.VERIFIER)
        self.ctx = ToolContext(user=self.user, conversation=Conversation.objects.create(user=self.user, title="t"))

    def args(self, **over):
        return {"title": "Манометры — МИ 2124-90", "family": self.family.pk, "plan": copy.deepcopy(MANOMETER),
                "sources": ["МИ 2124-90, пп. 5.1–5.4"], **over}

    def test_card_with_preview_then_confirm(self):
        from apps.assistant import actions
        from apps.assistant.models import Attachment, PendingAction
        from apps.assistant.tools import ei_verification_template_propose

        result = ei_verification_template_propose(self.ctx, self.args())
        action = PendingAction.objects.get(pk=result.ui["action"])
        assert action.kind == "verification_template_save" and not VerificationTemplate.objects.exists()
        assert result.ui["files"] and Attachment.objects.filter(pk=result.ui["files"][0]["id"]).exists()
        actions.decide(action, self.user, confirm=True)
        action.refresh_from_db()
        assert action.status == "done", action.error
        template = VerificationTemplate.objects.get()
        assert template.needs_review and template.created_via_assistant and template.sources

    def test_refuses_instance_data_and_requires_sources(self):
        from apps.assistant.tools import ToolFailure, ei_verification_template_propose

        with pytest.raises(ToolFailure, match="только структура"):
            ei_verification_template_propose(self.ctx, self.args(readings={}))
        with pytest.raises(ToolFailure, match="sources"):
            ei_verification_template_propose(self.ctx, self.args(sources=[]))
        with pytest.raises(ToolFailure, match="family"):
            ei_verification_template_propose(self.ctx, self.args(family=999))

    def test_check_against_example(self):
        from apps.assistant.tools import ei_verification_template_check

        example = {"source": "образец", "params": {"upper": "1.6", "cls": "1.5"},
                   "modes": {"p50": {"readings": {"up": ["0.81"], "down": ["0.80"]},
                                     "expected": {"err_up": "0,010", "variation": "0,010"}}}}
        result = ei_verification_template_check(self.ctx, {"plan": copy.deepcopy(MANOMETER), "example": example})
        assert "Все формулы сходятся" in result.content, result.content
