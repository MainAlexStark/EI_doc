"""Шаблоны планов аттестации по виду ИО: из аттестации, проверка, применение к новому ИО."""

from __future__ import annotations

import copy

from django.urls import reverse

from apps.equipment import service
from apps.equipment.models import AttestationProgram, EquipmentAttestation
import json

from apps.equipment.tests.test_equipment import FRIDGE_PLAN, GOLDEN, AttestationTestCase, card

NEW_FRIDGE = {
    "name": "Холодильник фармацевтический ХФ-250 «ПОЗИС»", "serial_number": "NEW-001",
    "manufacturer": "АО «ПО «Завод имени Серго»", "year": "2022", "owner": "ГБУЗ «Кировская больница»",
}


class ProgramTemplateTests(AttestationTestCase):
    def test_save_attestation_plan_as_template_and_apply_to_new_equipment(self):
        source = service.create(self.metrologist, card())  # план метролога — проверен
        r = self.as_(self.metrologist).post(reverse("attestation_save_as_program", args=[source.pk]),
                                            {"equipment_kind": "Холодильники фармацевтические"}, format="json")
        assert r.status_code == 201, r.data
        program = AttestationProgram.objects.get(pk=r.data["id"])
        assert program.source_attestation_id == source.pk and program.plan == source.plan
        assert r.data["status"] == "reviewed" and r.data["reviewed_by"]
        assert "по аттестации" in program.title

        r = self.as_(self.verifier).post(reverse("attestation_program_apply", args=[program.pk]),
                                         {"equipment": NEW_FRIDGE, "kind": "primary"}, format="json")
        assert r.status_code == 201, r.data
        att = EquipmentAttestation.objects.get(pk=r.data["id"])
        assert att.program_id == program.pk and att.plan == program.plan
        assert att.equipment["serial_number"] == "NEW-001" and att.kind == "primary"
        assert att.plan_approved  # шаблон проверен — план тоже
        assert att.measurements == {}

    def test_verifier_template_is_draft_until_metrologist_approves(self):
        source = service.create(self.verifier, card())
        r = self.as_(self.verifier).post(reverse("attestation_save_as_program", args=[source.pk]), {}, format="json")
        assert r.status_code == 201 and r.data["status"] == "draft"
        pk = r.data["id"]
        att = self.as_(self.verifier).post(reverse("attestation_program_apply", args=[pk]),
                                           {"equipment": NEW_FRIDGE}, format="json").data
        assert att["plan_approved"] is False

        assert self.as_(self.verifier).post(reverse("attestation_program_approve", args=[pk])).status_code == 403
        r = self.as_(self.metrologist).post(reverse("attestation_program_approve", args=[pk]))
        assert r.status_code == 200 and r.data["status"] == "reviewed"

        drafts = self.client.get(reverse("attestation_programs"), {"status": "draft", "plan": "0"}).data
        assert pk not in [p["id"] for p in drafts] and all("plan" not in p for p in drafts)

    def test_list_filters_by_kind_and_counts_usage(self):
        program = service.save_program(self.metrologist, {"title": "Термостаты — типовой", "equipment_kind": "Термостаты",
                                                          "plan": copy.deepcopy(FRIDGE_PLAN)}, via_assistant=False)
        service.apply_program(program, self.verifier, {"equipment": NEW_FRIDGE})
        rows = self.as_(self.verifier).get(reverse("attestation_programs"), {"equipment_kind": "Термостат"}).data
        assert [p["id"] for p in rows] == [program.pk] and rows[0]["used"] == 1

    def test_patch_by_verifier_drops_review(self):
        program = service.save_program(self.metrologist, {"title": "Шкафы сушильные", "equipment_kind": "Шкафы",
                                                          "plan": copy.deepcopy(FRIDGE_PLAN)}, via_assistant=False)
        assert not program.needs_review
        r = self.as_(self.verifier).patch(reverse("attestation_program", args=[program.pk]),
                                          {"note": "уточнить режимы"}, format="json")
        assert r.status_code == 200 and r.data["status"] == "draft" and r.data["note"] == "уточнить режимы"

    def test_plan_is_required(self):
        att = service.create(self.metrologist, card())
        EquipmentAttestation.objects.filter(pk=att.pk).update(plan={})
        r = self.as_(self.metrologist).post(reverse("attestation_save_as_program", args=[att.pk]), {}, format="json")
        assert r.status_code == 400

    def test_check_endpoint_reports_without_storing_example_numbers(self):
        program = service.save_program(self.metrologist, {"title": "Холодильники — сверка", "equipment_kind": "Холодильники",
                                                          "plan": copy.deepcopy(FRIDGE_PLAN)}, via_assistant=False)
        assert self.as_(self.verifier).get(reverse("attestation_program", args=[program.pk])).data["plan_check"] is None
        example = {"source": "протокол 1886/2026", "modes": {"m1": {
            "display": "5", "readings": copy.deepcopy(GOLDEN["readings"]),
            "expected": {"t_reached": "5,69", "amplitude": "0,26"}}}}
        url = reverse("attestation_program_check", args=[program.pk])
        r = self.as_(self.verifier).post(url, {"example": example}, format="json")
        assert r.status_code == 200, r.data
        check = r.data["plan_check"]
        assert check["ok"] is False and [x["key"] for x in check["rows"] if not x["ok"]] == ["amplitude"]
        assert check["checked_by"] and check["source"] == "протокол 1886/2026"
        program.refresh_from_db()
        assert program.plan_check["rows"] and "readings" not in json.dumps(program.plan_check)
        assert "6.031" not in json.dumps(program.plan_check)  # отсчёты примера не хранятся
        assert not EquipmentAttestation.objects.exists()

        assert self.as_(self.verifier).post(url, {}, format="json").status_code == 400
        bad = {"modes": {"nope": {"readings": {}}}}
        assert self.as_(self.verifier).post(url, {"example": bad}, format="json").status_code == 400

        # Правка плана — сверка «устарела», правка примечания — нет.
        r = self.as_(self.metrologist).patch(reverse("attestation_program", args=[program.pk]), {"note": "x"}, format="json")
        assert r.data["plan_check"].get("stale") is None
        plan = copy.deepcopy(program.plan)
        plan["repeats"] = plan["repeats"] + 1
        r = self.as_(self.metrologist).patch(reverse("attestation_program", args=[program.pk]), {"plan": plan}, format="json")
        assert r.status_code == 200, r.data
        assert r.data["plan_check"]["stale"] is True

    def test_patch_keeps_disabled_template_disabled(self):
        program = service.save_program(self.metrologist, {"title": "Печи", "equipment_kind": "Печи",
                                                          "plan": copy.deepcopy(FRIDGE_PLAN)}, via_assistant=False)
        url = reverse("attestation_program", args=[program.pk])
        assert self.as_(self.metrologist).patch(url, {"is_active": False}, format="json").data["is_active"] is False
        r = self.as_(self.metrologist).patch(url, {"note": "уточнить"}, format="json")
        assert r.status_code == 200 and r.data["is_active"] is False
