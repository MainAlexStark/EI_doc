"""«Перечень применяемых эталонов»: импорт из Word, правка, набор в поверке и журнале."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog import standard_kits as kits
from apps.catalog.models import AuxInstrument, MeasurementFamily, StandardKit
from apps.core.models import Role, User
from apps.verification import journal, templates
from apps.verification.tests import factories as f
from apps.verification.tests.test_templates import GOOD, MANOMETER

DOCX = Path(__file__).parent / "fixtures" / "standard_kits.docx"


class ImportTests(TestCase):
    def setUp(self):
        self.manometers = MeasurementFamily.objects.create(code="manometers", name="Манометры")
        self.water = MeasurementFamily.objects.create(code="water", name="Счётчики воды", calculator_key="water_meter")
        MeasurementFamily.objects.create(code="gas", name="Счётчики газа")

    def test_parse_company_document(self):
        parsed = kits.parse_docx(DOCX.read_bytes())
        sections = {k["section"] for k in parsed["kits"]}
        assert "Единый перечень для поверки манометров" in sections
        assert "Перечень для поверки весов. Бурков Николай Евгеньевич" in sections
        manometer = next(k for k in parsed["kits"] if k["section"].endswith("манометров"))
        assert manometer["fif_numbers"] == ["68984.17.4Р.01152950"]
        assert "69551-17^871455^" in manometer["journal_aux"]
        heavy = [k for k in parsed["kits"] if k["section"].startswith("Наборы до 3000")]
        assert heavy and heavy[0]["note"].startswith("Уведомлять")
        thermometer = next(a for a in parsed["aux"] if a["name"] == "Термометр ТЛ-5 №22")
        assert thermometer["designation"] == "299-11^22^"
        assert any(not a["is_active"] for a in parsed["aux"])  # «пока что не поверены»

    def test_import_twice_keeps_ids_and_guesses_families(self):
        report = kits.import_docx(DOCX.read_bytes())
        assert report["kits_created"] == StandardKit.objects.count() > 50
        kit = StandardKit.objects.filter(section__endswith="манометров").order_by("sort").first()
        assert list(kit.families.all()) == [self.manometers]
        water_kit = StandardKit.objects.get(section__contains="счетчиков воды")
        assert list(water_kit.families.all()) == [self.water]  # «Счётчики газа» не угадались

        kit.standards_text = "поправлено руками"
        kit.save()
        again = kits.import_docx(DOCX.read_bytes())
        assert again["kits_created"] == 0 and again["kits_disabled"] == 0
        assert StandardKit.objects.filter(pk=kit.pk, standards_text__startswith="Манометр цифровой").exists()
        assert AuxInstrument.objects.filter(designation="299-11^22^").count() == 1

    def test_command(self):
        from django.core.management import call_command

        call_command("import_standard_kits", str(DOCX))
        assert StandardKit.objects.filter(is_active=True).exists()


class ChangesTests(TestCase):
    def setUp(self):
        self.family = MeasurementFamily.objects.create(code="manometers", name="Манометры")
        self.kit = StandardKit.objects.create(section="Единый перечень для поверки манометров", scope="КТ 1,5",
                                              fif_numbers=["68984.17.4Р.01152950"])
        self.metrologist = User.objects.create_user("m@ei.test", "pw", role=Role.METROLOGIST)
        self.verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)

    def test_add_update_remove_all_or_nothing(self):
        done = kits.apply_changes([
            {"op": "update", "id": self.kit.pk, "fif_numbers": "68984.17.4Р.01152950|82489.21.4Р.01568957"},
            {"op": "add", "target": "aux", "name": "Термометр ТЛ-4 №7", "designation": "299-11^7^"},
            {"op": "add", "section": "Единый перечень для поверки манометров", "scope": "вакуумметры"},
        ], self.metrologist)
        assert len(done) == 3
        self.kit.refresh_from_db()
        assert self.kit.fif_numbers == ["68984.17.4Р.01152950", "82489.21.4Р.01568957"]
        new = StandardKit.objects.get(scope="вакуумметры")
        assert list(new.families.all()) == [self.family]

        with pytest.raises(kits.KitError, match="не номер"):
            kits.apply_changes([{"op": "remove", "id": new.pk}, {"op": "update", "id": self.kit.pk,
                                                                   "fif_numbers": "абв"}], self.metrologist)
        assert StandardKit.objects.get(pk=new.pk).is_active  # первая правка откатилась

    def test_api_permissions_and_import(self):
        client = APIClient()
        client.force_authenticate(self.verifier)
        assert client.get(reverse("standard_kits")).data["can_edit"] is False
        url = reverse("standard_kit_detail", args=[self.kit.pk])
        assert client.patch(url, {"scope": "x"}, format="json").status_code == 403

        client.force_authenticate(self.metrologist)
        r = client.patch(url, {"scope": "КТ 1,5 и 2,5"}, format="json")
        assert r.status_code == 200 and r.data["scope"] == "КТ 1,5 и 2,5"
        r = client.patch(url, {"fif_numbers": ["нет"]}, format="json")
        assert r.status_code == 400 and "ФИФ ОЕИ" in str(r.data)
        upload = SimpleUploadedFile("перечень.docx", DOCX.read_bytes())
        r = client.post(reverse("standard_kits_import"), {"file": upload}, format="multipart")
        assert r.status_code == 200 and r.data["kits_created"] > 50
        self.kit.refresh_from_db()
        assert not self.kit.is_active  # строки «КТ 1,5 и 2,5» в документе нет — выключена

    def test_assistant_proposes_changes_with_card(self):
        from apps.assistant import actions
        from apps.assistant.models import Conversation, PendingAction
        from apps.assistant.tools import ToolContext, ToolFailure, ei_standard_kits_propose

        conv = Conversation.objects.create(user=self.metrologist)
        ctx = ToolContext(user=self.metrologist, conversation=conv)
        result = ei_standard_kits_propose(ctx, {"changes": [
            {"op": "update", "id": self.kit.pk, "aux_text": "Метеоскоп-М № 688323", "journal_aux": "32014-11^688323^|"}]})
        action = PendingAction.objects.get(pk=result.ui["action"])
        self.kit.refresh_from_db()
        assert self.kit.aux_text == ""  # до подтверждения ничего не изменилось
        actions.decide(action, self.metrologist, confirm=True)
        self.kit.refresh_from_db()
        assert self.kit.journal_aux == "32014-11^688323^|"
        with pytest.raises(ToolFailure, match="метролог"):
            ei_standard_kits_propose(ToolContext(user=self.verifier, conversation=conv),
                                     {"changes": [{"op": "remove", "id": self.kit.pk}]})


class KitInVerificationTests(TestCase):
    def setUp(self):
        self.family = f.make_family(code="manometers", name="Манометры")
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)
        self.si_type = f.make_si_type(self.family, registry_number="39153-08", name="ДМ",
                                      limits={"upper": 1.6, "accuracy_class": 1.5})
        self.verification = f.make_verification(self.family, self.employee, day=6, si_type=self.si_type)
        self.verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.kit = StandardKit.objects.create(
            section="Единый перечень для поверки манометров", scope="до 2,5 МПа",
            standards_text="Манометр цифровой ДМ5002М-А №0988", aux_text="Метеоскоп-М № 688323",
            journal_aux="69551-17^871455^|2231-72^8787^|\n\n+ (у электроконтактных манометров)\n32014-11^688323^|",
            fif_numbers=["68984.17.4Р.01152950"])
        self.other = StandardKit.objects.create(section="Единый перечень для поверки манометров", scope="до 0,25 МПа",
                                                fif_numbers=["82489.21.4Р.01568957"])
        self.template = templates.save_template(
            self.verifier, {"title": "Манометры — МИ 2124-90", "family": self.family.pk,
                            "plan": copy.deepcopy(MANOMETER), "kits": [self.kit.pk, self.other.pk]},
            sources=["МИ 2124-90"])
        self.client = APIClient()
        self.client.force_authenticate(self.verifier)

    def post(self, **over):
        data = {"operations": {"visual": True, "testing": True}, "readings": copy.deepcopy(GOOD), **over}
        return self.client.post(reverse("verification_measurements", args=[self.verification.pk]), data, format="json")

    def test_choose_kit_then_protocol_and_journal(self):
        info = self.client.get(reverse("verification_template_for", args=[self.verification.pk])).data
        assert [k["id"] for k in info["kits"]] == [self.kit.pk, self.other.pk]
        r = self.post()
        assert r.status_code == 400 and "набор эталонов" in str(r.data)
        r = self.post(kit=self.kit.pk)
        assert r.status_code == 200, r.data
        self.verification.refresh_from_db()
        assert self.verification.measurements["kit"]["id"] == self.kit.pk
        row = journal.row_values(self.verification)
        assert row["aux_instruments"] == "69551-17^871455^|2231-72^8787^|32014-11^688323^|"
        assert row["standards"] == "68984.17.4Р.01152950"
        names = [i["name"] for i in templates._standards(self.verification)]
        assert names == ["Манометр цифровой ДМ5002М-А №0988", "Метеоскоп-М № 688323"]

    def test_single_kit_is_taken_automatically(self):
        self.template.kits.set([self.kit])
        assert self.post().status_code == 200
        self.verification.refresh_from_db()
        assert self.verification.measurements["kit"]["scope"] == "до 2,5 МПа"
