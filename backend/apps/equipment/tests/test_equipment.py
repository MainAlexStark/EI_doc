"""Аттестация ИО: формулы, план, правила подписи, API, инструменты ассистента."""

from __future__ import annotations

import copy
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Role, User
from apps.equipment import calc, expr, service
from apps.equipment.models import (
    AttestationCounter,
    AttestationProgram,
    EquipmentAttestation,
)

GOLDEN = json.loads((Path(__file__).parent / "golden" / "protocol_1886.json").read_text(encoding="utf-8"))
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "programs.json").read_text(encoding="utf-8")
)
FRIDGE_PLAN = FIXTURE[0]["plan"]


def golden_measurements() -> dict:
    return {"m1": {"display": "5", "readings": copy.deepcopy(GOLDEN["readings"])}}


def card(**over) -> dict:
    data = {
        "kind": "periodic",
        "equipment": {
            "name": "Холодильник фармацевтический ХФ-140 «ПОЗИС»", "serial_number": "215CV10051768",
            "manufacturer": "АО «ПО «Завод имени Серго»", "year": "2019", "owner": "ФГБОУ ВО СибГМУ Минздрава России",
        },
        "commission": [
            {"role": "member", "position": "Инженер-метролог ООО «ЕДИНИЦА ИЗМЕРЕНИЯ»", "name": "Сунгуров И. В."},
            {"role": "chair", "position": "Инженер по метрологии", "name": "Ищенко Е. И."},
        ],
        "instruments": [{
            "name": "Термометр лабораторный электронный LTA/Б-Э", "serial_number": "871455",
            "characteristics": "(-50…200) °С, ПГ ±0,02 °С", "verification": "Свидетельство № С-БЭ/19-12-2025/490442737",
            "valid_until": "2026-12-18",
        }],
        "plan": copy.deepcopy(FRIDGE_PLAN),
    }
    data.update(over)
    return data


# ---------------------------------------------------------------------------
# Формулы — совпадение с Excel образца
# ---------------------------------------------------------------------------
class TestFormulasGolden:
    def test_matches_excel(self):
        plan = calc.clean_plan(FRIDGE_PLAN)
        result = calc.compute(plan, golden_measurements())
        rows = {c["key"]: Decimal(c["value"]) for c in result["modes"][0]["characteristics"]}
        for key, excel in GOLDEN["excel_results"].items():
            # Excel округляет средние по точкам до 4 знаков (столбец O) — отсюда допуск 1e-4
            assert abs(rows[key] - Decimal(str(excel))) < Decimal("0.0001"), key
        means = {p["key"]: Decimal(p["mean"]) for p in result["modes"][0]["points"]}
        for key, excel in GOLDEN["excel_point_means"].items():
            assert abs(means[key] - Decimal(excel)) < Decimal("0.00006"), key
        assert result["suitable"] is True

    def test_out_of_range_is_not_suitable(self):
        plan = calc.clean_plan(FRIDGE_PLAN)
        m = golden_measurements()
        m["m1"]["readings"] = {k: ["9.5"] * 10 for k in m["m1"]["readings"]}
        result = calc.compute(plan, m)
        assert result["suitable"] is False
        failed = [c["key"] for c in result["modes"][0]["characteristics"] if c["passed"] is False]
        assert failed == ["t_reached", "dt"]

    def test_tolerance_checked_on_rounded_value(self):
        rng = {"kind": "range", "min": Decimal(2), "max": Decimal(8)}
        assert calc.check(Decimal("8.004"), rng, 2) is True
        assert calc.check(Decimal("8.005"), rng, 2) is False
        assert calc.check(Decimal("-3.001"), {"kind": "abs", "limit": Decimal(3)}, 2) is True

    def test_fmt(self):
        assert calc.fmt(Decimal("5.6915"), 2) == "5,69"
        assert calc.fmt(Decimal("-0.0001"), 2) == "0,00"
        assert calc.fmt(Decimal(5), 1, sign=True) == "+5,0"

    def test_incomplete_grid_is_not_computed(self):
        plan = calc.clean_plan(FRIDGE_PLAN)
        m = golden_measurements()
        m["m1"]["readings"]["3"] = m["m1"]["readings"]["3"][:9]
        with pytest.raises(calc.Incomplete, match="точка 3: не внесено 1 из 10"):
            calc.compute(plan, m)


# ---------------------------------------------------------------------------
# Выражения
# ---------------------------------------------------------------------------
class TestExpr:
    def env(self):
        a, b, c = (expr.Vec(tuple(Decimal(x) for x in v)) for v in (["1", "3"], ["5", "7"], ["4", "4"]))
        volume = expr.Group("volume", (("1", a), ("2", b)))
        control = expr.Group("control", (("3", c),))
        every = expr.Group("all", (("1", a), ("2", b), ("3", c)))
        return {"all": every, "volume": volume, "control": control, "setpoint": Decimal(4), "display": None}, \
            {"1": a, "2": b, "3": c}

    @pytest.mark.parametrize("source, expected", [
        ("mean(all)", "4"),
        ("max(means(volume)) - min(means(volume))", "4"),
        ("(max(control) - min(control)) / 2", "0"),
        ("max(abs(all - setpoint))", "3"),
        ("mean(point(\"2\")) − setpoint", "2"),
        ("round(10 / 3, 2)", "3.33"),
        ("2^3 + sum(volume)", "24"),
        ("count(all) * 1", "6"),
        ("range(volume)", "6"),
    ])
    def test_values(self, source, expected):
        env, points = self.env()
        assert expr.evaluate_number(source, env, points) == Decimal(expected)

    def test_std(self):
        env, points = self.env()
        assert abs(expr.evaluate_number("std(volume)", env, points) - Decimal("2.5819888974716112")) < Decimal("1e-12")

    @pytest.mark.parametrize("source, message", [
        ("__import__('os').system('x')", "только по имени"),
        ("all.__class__", "недопустимая"),
        ("open('x')", "неизвестная функция"),
        ("mean(all", "не разобрана"),
        ("[1, 2]", "недопустимая"),
        ("lambda: 1", "недопустимая"),
        ("mean", "нужны скобки"),
    ])
    def test_rejects_code(self, source, message):
        with pytest.raises(expr.ExprError, match=message):
            expr.parse(source)

    def test_vector_result_is_not_a_number(self):
        env, points = self.env()
        with pytest.raises(expr.ExprError, match="ряд"):
            expr.evaluate_number("all - setpoint", env, points)

    def test_missing_display(self):
        env, points = self.env()
        with pytest.raises(expr.Missing):
            expr.evaluate_number("display - mean(all)", env, points)


# ---------------------------------------------------------------------------
# План
# ---------------------------------------------------------------------------
CHAMBER_PLAN = {
    "equipment_kind": "Камера климатическая",
    "program": "Программа аттестации камер (пример)",
    "repeats": 3,
    "points": [
        {"key": "A", "label": "верх", "groups": ["corners"]},
        {"key": "B", "label": "низ", "groups": ["corners"]},
        {"key": "C", "label": "центр", "role": "control"},
    ],
    "modes": [
        {"key": "cold", "label": "−20 °С", "setpoint": "-20", "params": {"tol": "2"}},
        {"key": "hot", "label": "+60 °С", "setpoint": "60", "params": {"tol": "1.5"}},
    ],
    "characteristics": [
        {"key": "reached", "label": "Достигнутое значение", "expr": "mean(all)", "text": "Т = Σt/n",
         "tolerance": {"kind": "range", "min": "setpoint - tol", "max": "setpoint + tol"}, "source": "программа, п. 7.2"},
        {"key": "dev_point", "label": "Отклонение в точке", "per": "point", "group": "corners",
         "expr": "mean(x) - setpoint", "tolerance": {"kind": "abs", "limit": "tol"}, "source": "программа, п. 7.3"},
        {"key": "uneven", "label": "Неравномерность", "expr": "max(abs(dev_point))", "decimals": 1},
    ],
    "layout": [
        {"type": "heading", "text": "Протокол для {{equipment.name}}"},
        {"type": "per_mode", "blocks": [
            {"type": "paragraph", "text": "Режим {{mode._.label}}: Т = {{result._.reached}} (допуск {{result._.reached|tolerance}})"},
            {"type": "paragraph", "text": "Верх: {{result._.dev_point.A}} — {{result._.dev_point.A|verdict}}"},
            {"type": "results", "columns": ["label", "value", "verdict"]},
        ]},
        {"type": "conclusion"},
    ],
    "conclusion": {"ok": "{{who}} годна.", "fail": "{{who}} не годна: {{failed}}."},
}


def chamber_measurements(hot_a="60.5"):
    return {
        "cold": {"readings": {"A": ["-20.5", "-19.5", "-20"], "B": ["-21", "-21", "-21"], "C": ["-20", "-20", "-20"]}},
        "hot": {"readings": {"A": [hot_a] * 3, "B": ["59.5", "60", "60.5"], "C": ["60", "60", "60"]}},
    }


class TestPlan:
    def test_fixture_is_valid(self):
        plan = calc.clean_plan(FRIDGE_PLAN)
        assert len(plan["points"]) == 7 and plan["repeats"] == 10
        assert plan["characteristics"][0]["expr"] == "mean(all)"

    @pytest.mark.parametrize("bad, message", [
        ({"measurements": {}}, "не может быть результатов измерений"),
        ({"repeats": 0}, "repeats"),
        ({"modes": []}, "нет режимов"),
    ])
    def test_rejects(self, bad, message):
        with pytest.raises(calc.PlanError, match=message):
            calc.clean_plan({**FRIDGE_PLAN, **bad})

    def test_unknown_preset(self):
        plan = copy.deepcopy(FRIDGE_PLAN)
        plan["characteristics"][0]["preset"] = "magic"
        with pytest.raises(calc.PlanError, match="напишите выражение"):
            calc.clean_plan(plan)

    def test_unknown_name_in_formula(self):
        plan = copy.deepcopy(FRIDGE_PLAN)
        plan["characteristics"][0] = {**plan["characteristics"][0], "preset": None, "expr": "mean(walls)", "label": "Т"}
        with pytest.raises(calc.PlanError, match="неизвестные имена walls"):
            calc.clean_plan(plan)

    def test_tolerance_needs_source(self):
        plan = copy.deepcopy(FRIDGE_PLAN)
        plan["characteristics"][0]["source"] = ""
        with pytest.raises(calc.PlanError, match="не указан источник"):
            calc.clean_plan(plan)

    def test_formula_on_empty_group_caught_by_dry_run(self):
        plan = copy.deepcopy(FRIDGE_PLAN)
        plan["points"] = [p for p in plan["points"] if p["role"] == "volume"]
        with pytest.raises(calc.PlanError, match="нет точек"):
            calc.clean_plan(plan)

    def test_custom_plan_modes_params_per_point(self):
        plan = calc.clean_plan(CHAMBER_PLAN)
        result = calc.compute(plan, chamber_measurements())
        cold = result["modes"][0]
        rows = {(r["key"], r["point"]): r for r in cold["characteristics"]}
        assert rows[("reached", "")]["shown"] == "-20,33" and rows[("reached", "")]["tolerance"] == "от -22 до -18"
        assert rows[("dev_point", "B")]["shown"] == "-1,00" and rows[("dev_point", "B")]["tolerance"] == "±2"
        assert rows[("uneven", "")]["shown"] == "1,0"
        assert result["suitable"] is True
        failed = calc.compute(plan, chamber_measurements(hot_a="61.6"))
        bad = [(r["key"], r["point"]) for m in failed["modes"] for r in m["characteristics"] if r["passed"] is False]
        assert bad == [("dev_point", "A")]

    def test_layout_rejects_unknown_placeholder(self):
        plan = copy.deepcopy(CHAMBER_PLAN)
        plan["layout"] = [{"type": "paragraph", "text": "{{result._.nope}}"}]
        with pytest.raises(calc.PlanError, match="_.*per_mode|нет характеристики"):
            calc.clean_plan(plan)
        plan["layout"] = [{"type": "paragraph", "text": "{{result.cold.dev_point}}"}]
        with pytest.raises(calc.PlanError, match="укажите точку"):
            calc.clean_plan(plan)


class TestExampleCheck:
    def example(self, **expected):
        return {
            "source": "протокол 1886/2026",
            "modes": {"m1": {"display": "5", "readings": copy.deepcopy(GOLDEN["readings"]), "expected": expected}},
        }

    def test_golden_example_matches(self):
        plan = calc.clean_plan(FRIDGE_PLAN)
        report = calc.check_example(plan, self.example(
            t_reached="5,6915", dt="0,6915", amplitude="0,5170", var_max="-0,0987", var_min="0,0740",
            gradient="0,1727",
        ))
        assert report["ok"], report
        assert report["unchecked"] == []

    def test_wrong_formula_is_caught(self):
        raw = copy.deepcopy(FRIDGE_PLAN)
        amp = next(c for c in raw["characteristics"] if c["key"] == "amplitude")
        amp.pop("preset")
        amp.update(expr="max(control) - min(control)", label="Амплитуда")  # забыли «/ 2»
        report = calc.check_example(calc.clean_plan(raw), self.example(amplitude="0,52", t_reached="5,69"))
        assert not report["ok"]
        assert [r["key"] for r in report["rows"] if not r["ok"]] == ["amplitude"]

    def test_rounded_printed_values(self):
        plan = calc.clean_plan(FRIDGE_PLAN)
        assert calc.check_example(plan, self.example(t_reached="5,69", gradient="0,17"))["ok"]


class TestLayout:
    def test_custom_layout_renders(self):
        from apps.equipment import layout

        plan = calc.clean_plan(CHAMBER_PLAN)
        measurements = chamber_measurements()
        ctx = layout.Context(
            plan=plan, equipment={"name": "Камера КТК-800", "serial_number": "17"}, commission=[], instruments=[],
            texts={}, conditions={}, measurements=measurements, results=calc.compute(plan, measurements),
            kind="periodic", number=None, protocol_date=None, performed_on=None,
        )
        spec = layout.build(ctx, draft=True)
        texts = [b.get("text") for b in spec["blocks"] if b["type"] in ("heading", "paragraph")]
        assert texts[0] == "Протокол для Камера КТК-800"
        assert "Режим −20 °С: Т = -20,33 (допуск от -22 до -18)" in texts
        assert "Верх: 0,00 — соответствует" in texts
        assert texts[-1] == "камера КТК-800, заводской номер 17 годна."
        tables = [b for b in spec["blocks"] if b["type"] == "table"]
        assert tables[0]["columns"] == ["Характеристика", "Полученное значение", "Соответствие"]
        assert spec["subtitle"] == "№ ________ от ________"


# ---------------------------------------------------------------------------
# Сервис и API
# ---------------------------------------------------------------------------
class AttestationTestCase(TestCase):
    def setUp(self):
        self.verifier = User.objects.create_user(email="v@ei.ru", password="x", role=Role.VERIFIER)
        self.metrologist = User.objects.create_user(email="m@ei.ru", password="x", role=Role.METROLOGIST)
        self.observer = User.objects.create_user(email="o@ei.ru", password="x", role=Role.OBSERVER)
        self.client = APIClient()

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def measured(self, att, user=None, **over):
        payload = {
            "measurements": golden_measurements(),
            "conditions": {"temperature": "21,4", "humidity": "52.8", "pressure": "99"},
            "performed_on": (timezone.localdate() - dt.timedelta(days=1)).isoformat(),
        }
        payload.update(over)
        return service.save_measurements(att, user or self.verifier, payload)[0]


class ServiceTests(AttestationTestCase):
    def test_seeded_program_exists(self):
        program = AttestationProgram.objects.get()
        assert program.needs_review
        calc.clean_plan(program.plan)

    def test_verifier_plan_needs_review_metrologist_plan_does_not(self):
        assert not service.create(self.verifier, card()).plan_approved
        assert service.create(self.metrologist, card()).plan_approved

    def test_observer_cannot_create(self):
        from rest_framework.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            service.create(self.observer, card())

    def test_full_cycle(self):
        att = service.create(self.verifier, card())
        att = self.measured(att)
        problems = service.sign_problems(att, timezone.localdate())
        assert problems == ["план аттестации не проверен метрологом"]
        service.approve_plan(att, self.metrologist)
        AttestationCounter.objects.create(year=timezone.localdate().year, last=1885)
        att = service.sign(att, self.metrologist)
        assert att.full_number == f"1886/{timezone.localdate().year}"
        assert att.suitable is True and att.sha256 and att.snapshot["draft"] is False
        conclusion = att.snapshot["blocks"][-4]["text"]
        assert "соответствует требованиям эксплуатационной документации и может использоваться" in conclusion
        with pytest.raises(service.AttestationError, match="подписан"):
            service.update(att, self.metrologist, {"texts": {"notes": "x"}})

    def test_cannot_sign_with_future_date_or_expired_instrument(self):
        data = card()
        data["instruments"][0]["valid_until"] = "2020-01-01"
        att = self.measured(service.create(self.metrologist, data))
        problems = service.sign_problems(att, timezone.localdate() + dt.timedelta(days=5))
        assert any("ещё не наступила" in p for p in problems)
        assert any("поверка истекла" in p for p in problems)

    def test_cannot_sign_incomplete(self):
        att = service.create(self.metrologist, card())
        m = golden_measurements()
        m["m1"]["readings"]["7"] = []
        att = self.measured(att, measurements=m)
        with pytest.raises(service.AttestationError, match="измерения не полные"):
            service.sign(att, self.metrologist)

    def test_future_measurement_date_rejected(self):
        att = service.create(self.metrologist, card())
        with pytest.raises(service.AttestationError, match="не наступила"):
            self.measured(att, performed_on=(timezone.localdate() + dt.timedelta(days=3)).isoformat())

    def test_plan_change_resets_approval(self):
        att = service.create(self.metrologist, card())
        plan = copy.deepcopy(FRIDGE_PLAN)
        plan["periodicity"] = "один раз в два года"
        att = service.update(att, self.verifier, {"plan": plan})
        assert not att.plan_approved

    def test_docx_renders(self):
        att = self.measured(service.create(self.metrologist, card()))
        data = service.render(att, "docx")
        assert data[:2] == b"PK"


class ApiTests(AttestationTestCase):
    def test_create_from_program_and_enter_measurements(self):
        program = AttestationProgram.objects.get()
        data = card()
        data.pop("plan")
        r = self.as_(self.verifier).post(reverse("attestation_list"), {**data, "program": program.pk}, format="json")
        assert r.status_code == 201, r.data
        pk = r.data["id"]
        assert r.data["calc"]["complete"] is False
        r = self.client.put(reverse("attestation_measurements", args=[pk]), {
            "measurements": golden_measurements(),
            "conditions": {"temperature": "21.4", "humidity": "52.8", "pressure": "99.0"},
            "performed_on": timezone.localdate().isoformat(),
        }, format="json")
        assert r.status_code == 200, r.data
        assert r.data["calc"]["results"]["suitable"] is True
        assert r.data["sign_problems"] == ["план аттестации не проверен метрологом"]
        r = self.client.post(reverse("attestation_sign", args=[pk]), {}, format="json")
        assert r.status_code == 403
        self.as_(self.metrologist)
        assert self.client.post(reverse("attestation_approve_plan", args=[pk])).status_code == 200
        r = self.client.post(reverse("attestation_sign", args=[pk]), {}, format="json")
        assert r.status_code == 200, r.data
        assert r.data["status"] == "signed" and r.data["number"].startswith("1/")
        r = self.client.get(reverse("attestation_protocol", args=[pk, "docx"]))
        assert r.status_code == 200 and r.content[:2] == b"PK"

    def test_list_search(self):
        service.create(self.verifier, card())
        r = self.as_(self.observer).get(reverse("attestation_list"), {"q": "215CV"})
        assert len(r.data) == 1


# ---------------------------------------------------------------------------
# Ассистент
# ---------------------------------------------------------------------------
class AssistantToolTests(AttestationTestCase):
    def setUp(self):
        super().setUp()
        from apps.assistant.models import Conversation
        from apps.assistant.tools import ToolContext

        self.conversation = Conversation.objects.create(user=self.verifier, title="t")
        self.ctx = ToolContext(user=self.verifier, conversation=self.conversation)

    def test_propose_creates_pending_action_then_confirm(self):
        from apps.assistant import actions
        from apps.assistant.models import PendingAction
        from apps.assistant.tools import ei_attestation_propose

        result = ei_attestation_propose(self.ctx, {**card(), "sources": ["шильдик: зав. № 215CV10051768"]})
        action = PendingAction.objects.get(pk=result.ui["action"])
        assert action.kind == "attestation_create"
        assert not EquipmentAttestation.objects.exists()
        actions.decide(action, self.verifier, confirm=True)
        action.refresh_from_db()
        assert action.status == "done", action.error
        att = EquipmentAttestation.objects.get()
        assert att.created_via_assistant and not att.measurements and not att.plan_approved
        assert att.sources == ["шильдик: зав. № 215CV10051768"]

    def test_propose_refuses_measurements(self):
        from apps.assistant.tools import ToolFailure, ei_attestation_propose

        data = card()
        data["plan"]["measurements"] = {"m1": {}}
        with pytest.raises(ToolFailure, match="не может быть результатов измерений"):
            ei_attestation_propose(self.ctx, {**data, "sources": ["x"]})
        with pytest.raises(ToolFailure, match="измерения вносит человек"):
            ei_attestation_propose(self.ctx, {**card(), "measurements": {}, "sources": ["x"]})

    def test_propose_requires_sources(self):
        from apps.assistant.tools import ToolFailure, ei_attestation_propose

        with pytest.raises(ToolFailure, match="sources"):
            ei_attestation_propose(self.ctx, card())

    def test_programs_and_search_tools(self):
        from apps.assistant.tools import ei_attestations

        service.create(self.verifier, card())
        result = ei_attestations(self.ctx, {"what": "programs"})
        assert "Холодильники фармацевтические" in result.content
        result = ei_attestations(self.ctx, {"what": "attestations", "query": "215CV10051768"})
        assert "215CV10051768" in result.content
        assert "mean_all" in ei_attestations(self.ctx, {"what": "formulas"}).content

    def test_check_tool_reports_without_saving(self):
        from apps.assistant.tools import ei_attestation_check

        example = {"source": "образец", "modes": {"m1": {"readings": copy.deepcopy(GOLDEN["readings"]),
                                                          "expected": {"t_reached": "5,69", "amplitude": "0,26"}}}}
        result = ei_attestation_check(self.ctx, {"plan": copy.deepcopy(FRIDGE_PLAN), "example": example})
        assert "Есть расхождения" in result.content and "amplitude" in result.content
        assert result.ui["widgets"][0]["type"] == "table"
        assert not EquipmentAttestation.objects.exists()

    def test_propose_keeps_check_report_not_readings(self):
        from apps.assistant import actions
        from apps.assistant.models import PendingAction
        from apps.assistant.tools import ei_attestation_propose

        example = {"modes": {"m1": {"readings": copy.deepcopy(GOLDEN["readings"]), "expected": {"t_reached": "5,69"}}}}
        result = ei_attestation_propose(self.ctx, {**card(), "example": example, "sources": ["образец"]})
        action = PendingAction.objects.get(pk=result.ui["action"])
        actions.decide(action, self.verifier, confirm=True)
        att = EquipmentAttestation.objects.get()
        assert att.plan_check["ok"] is True and att.measurements == {}
        assert "6.031" not in json.dumps(att.plan_check)

