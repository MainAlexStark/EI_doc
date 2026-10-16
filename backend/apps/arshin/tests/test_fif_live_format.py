"""Клиент ФИФ на живом формате ответов ФГИС (фикстуры сняты 16.10.2026)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest import mock

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.arshin import documents, limits
from apps.arshin.fif import FifCard, FifClient, actual_mpi, split_notation
from apps.arshin.models import FifDocument
from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Role, User

SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "fif_samples.json").read_text(encoding="utf-8"))


def fake_response(payload=None, *, content=b"", headers=None):
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    response.content = content
    response.headers = headers or {}
    return response


def tiny_pdf(text_lines: list[str]) -> bytes:
    """Минимальный PDF с текстом — чтобы проверить текстовый путь pypdfium2."""
    stream = "BT /F1 9 Tf 20 800 Td 11 TL " + " ".join(f"({line}) '" for line in text_lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


class NotationTestCase(SimpleTestCase):
    def test_groups_with_modifications(self):
        assert split_notation(
            "СХВ (СХВ-15, СХВ-15Д, СХВ-20, СХВ-20Д), СГВ (СГВ-15, СГВ-15Д, СГВ-20, СГВ-20Д)"
        ) == ["СХВ-15", "СХВ-15Д", "СХВ-20", "СХВ-20Д", "СГВ-15", "СГВ-15Д", "СГВ-20", "СГВ-20Д"]

    def test_plain_and_prefixed(self):
        assert split_notation("типа СВК") == ["СВК"]
        assert split_notation("СВК, СВКМ") == ["СВК", "СВКМ"]
        assert split_notation("ЭКВАТЭЛ СВК-15У") == ["ЭКВАТЭЛ СВК-15У"]
        assert split_notation("") == []


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class SearchTestCase(SimpleTestCase):
    def setUp(self) -> None:
        from django.core.cache import cache

        cache.clear()

    @mock.patch("apps.arshin.fif.time.sleep")
    @mock.patch("apps.arshin.fif.requests.get")
    def test_multiword_query_sends_one_word_and_filters_the_rest(self, get, _sleep):
        get.return_value = fake_response(SAMPLES["search_svk"])
        found = FifClient().search_types("СВК 15")
        assert get.call_args.kwargs["params"]["search"] == "*СВК*"
        assert [item.registry_number for item in found][:2] == ["90502-23", "81271-21"]
        assert len(found) == 5, "несовпавшие по второму слову не выбрасываются — модификации в реестре часто нет"
        assert found[0].uuid == "65dd276c-9d79-77a2-2ddf-4fea05e00c87"
        assert found[0].title == "Счетчики воды крыльчатые универсальные"
        assert found[0].name == "СВК-15У"

    @mock.patch("apps.arshin.fif.time.sleep")
    @mock.patch("apps.arshin.fif.requests.get")
    def test_digits_only_word_is_not_the_anchor(self, get, _sleep):
        get.return_value = fake_response(SAMPLES["search_svk"])
        FifClient().search_types("15-3-2 СВК")
        assert get.call_args.kwargs["params"]["search"] == "*СВК*"

    @mock.patch("apps.arshin.fif.time.sleep")
    @mock.patch("apps.arshin.fif.requests.get")
    def test_single_word_is_wrapped_in_wildcards(self, get, _sleep):
        get.return_value = fake_response(SAMPLES["search_svk"])
        found = FifClient().search_types("СВК")
        assert get.call_args.kwargs["params"]["search"] == "*СВК*"
        assert len(found) == 5
        assert found[4].name == "СВК" and "АПЗ" in found[4].manufacturer


class CardTestCase(SimpleTestCase):
    def test_puls_card(self):
        card = FifClient.parse_card(SAMPLES["card_puls"])
        assert card.registry_number == "68501-17"
        assert card.variants == ["ПУЛЬС"]
        assert card.valid_to == dt.date(2027, 9, 5)
        assert sorted({m["months"] for m in card.mpi}) == [48, 72]
        assert [(d.kind, d.title) for d in card.documents] == [("meth", "Методика поверки"), ("spec", "Описание типа")]
        assert card.documents[0].doc_uuid == "5ba71ac5-5c43-6b91-582b-6f55667c3034"

    def test_only_latest_mpi_counts(self):
        card = FifClient.parse_card(SAMPLES["card_svk"])
        assert [m["months"] for m in card.mpi] == [72]
        assert card.variants == ["СВК"]

    def test_expired_certificate_and_many_modifications(self):
        card = FifClient.parse_card(SAMPLES["card_betar_old"])
        assert card.is_valid is False
        assert "СГВ-20" in card.variants and len(card.variants) == 8
        assert "Чистополь" in card.manufacturer
        assert [d.kind for d in card.documents] == ["spec"]

    def test_mcp_wrapper_is_understood_too(self):
        card = FifClient.parse_card({"card": SAMPLES["card_svk"]["result"]})
        assert card.registry_number == "13869-13"

    def test_actual_mpi_ignores_garbage(self):
        assert actual_mpi([{"mpi": "нет"}, "строка"]) == []


def card(name: str) -> FifCard:
    return FifClient.parse_card(SAMPLES[name])


@override_settings(MEDIA_ROOT="/tmp/ei-test-media", SENDFILE_ROOT="/tmp/ei-test-media")
class ApiTestCase(TestCase):
    def setUp(self) -> None:
        self.family = MeasurementFamily.objects.create(code="water-meter", name="Счётчики воды", calculator_key="water_meter")
        self.user = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @mock.patch("apps.catalog.api_fif.FifClient")
    def test_card_registers_documents_and_lists_existing_types(self, client_cls):
        client_cls.return_value.get_type.return_value = card("card_svk")
        SiType.objects.create(family=self.family, registry_number="13869-13", name="СВК 15-3-2")
        data = self.client.get(reverse("fif_type_card", args=["13869-13"])).data
        assert data["variants"] == ["СВК"]
        assert data["valid_to"] == "2028-08-10"
        assert [d["kind"] for d in data["documents"]] == ["meth", "spec"]
        assert data["already_added"][0]["name"] == "СВК 15-3-2"
        assert FifDocument.objects.filter(mit_uuid="79ca313c-b94a-2229-e41a-573ba91c005d").count() == 2

    @mock.patch("apps.catalog.api_fif.FifClient")
    def test_registry_unavailable_is_a_clear_503(self, client_cls):
        from apps.arshin.fif import FifUnavailable

        client_cls.return_value.get_type.side_effect = FifUnavailable("Реестр ФИФ недоступен: timeout")
        response = self.client.get(reverse("fif_type_card", args=["13869-13"]))
        assert response.status_code == 503 and "недоступен" in response.data["detail"]

    @override_settings(FIF_DOCUMENTS_AUTO_DOWNLOAD=True)
    @mock.patch("apps.arshin.tasks.download_type_documents.delay")
    def test_creating_type_from_fif_schedules_documents(self, delay):
        body = {
            "registry_number": "68501-17", "name": "ПУЛЬС", "fif_uuid": "c362cd27-6d3c-7887-f820-cb2a86b6d34e",
            "type_title": "Счетчики воды", "certificate_valid_to": "2027-09-05",
            "limits": {"q_min": 0.03, "q_transition_a": 0.15, "q_transition_b": 0.12, "q_nominal": 1.5,
                       "q_max": 3, "error_below_transition": 5, "error_above_transition": 2},
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("si_type_list"), body, format="json")
        assert response.status_code == 201, response.data
        si_type = SiType.objects.get(registry_number="68501-17")
        assert si_type.certificate_valid_to == dt.date(2027, 9, 5) and si_type.type_title == "Счетчики воды"
        delay.assert_called_once_with("c362cd27-6d3c-7887-f820-cb2a86b6d34e")

    def test_document_downloaded_once_and_served(self):
        doc = documents.register_documents(card("card_puls"))[0]
        client = mock.Mock()
        client.download.return_value = (b"%PDF-1.4 fake", "MP_68501-17.pdf", "application/pdf")
        documents.download(doc, client=client)
        documents.download(doc, client=client)
        assert client.download.call_count == 1
        doc.refresh_from_db()
        assert doc.size == len(b"%PDF-1.4 fake") and doc.filename == "MP_68501-17.pdf"

        si_type = SiType.objects.create(
            family=self.family, registry_number="68501-17", name="ПУЛЬС", fif_uuid=doc.mit_uuid,
        )
        listed = self.client.get(reverse("si_type_documents", args=[si_type.pk])).data
        assert {d["kind"] for d in listed} == {"meth", "spec"}
        assert next(d for d in listed if d["kind"] == "meth")["downloaded"] is True
        response = self.client.get(reverse("fif_document_file", args=[doc.pk]))
        assert response.status_code == 200

    @mock.patch("apps.arshin.documents.FifClient")
    def test_not_downloadable_document_returns_link(self, client_cls):
        from apps.arshin.fif import FifUnavailable

        client_cls.return_value.download.side_effect = FifUnavailable("timeout")
        doc = documents.register_documents(card("card_puls"))[1]
        response = self.client.get(reverse("fif_document_file", args=[doc.pk]))
        assert response.status_code == 404
        assert response.data["url"].startswith("https://fgis.gost.ru/")


@override_settings(MEDIA_ROOT="/tmp/ei-test-media")
class LimitsTestCase(TestCase):
    def _spec_with(self, content: bytes) -> FifCard:
        result = card("card_puls")
        for doc in documents.register_documents(result):
            if doc.kind == "spec":
                doc.file.save("spec.pdf", ContentFile(content))
        return result

    def test_text_pdf_goes_as_text_and_values_come_back(self):
        lines = ["Metrological characteristics, flow rate m3/h"] + [
            f"DN15 Qmin 0,03 Qt 0,12 Qn 1,5 Qmax 3,0 row {i}" for i in range(20)
        ]
        result_card = self._spec_with(tiny_pdf(lines))
        model = mock.Mock(is_configured=True)
        model.complete_json.return_value = {
            "found": True, "dn": 15, "q_min": 0.03, "q_transition_b": 0.12, "q_transition_a": 0.15,
            "q_nominal": 1.5, "q_max": 3, "error_below_transition": 5, "error_above_transition": 2,
            "quote": "DN15 Qmin 0,03 Qt 0,12", "comment": None,
        }
        result = limits.extract_limits(result_card, "ПУЛЬС", client=model)
        parts = model.complete_json.call_args.args[0]
        assert result["mode"] == "text" and "DN15 Qmin" in parts[1]["text"]
        assert "ПУЛЬС" in parts[0]["text"]
        assert result["found"] and result["missing"] == [] and result["limits"]["q_max"] == 3.0

    def test_scanned_pdf_goes_as_images(self):
        result_card = self._spec_with(tiny_pdf([]))
        model = mock.Mock(is_configured=True)
        model.complete_json.return_value = {"found": False}
        result = limits.extract_limits(result_card, "ПУЛЬС", client=model)
        parts = model.complete_json.call_args.args[0]
        assert result["mode"] == "images" and parts[1]["type"] == "image_bytes"
        assert result["found"] is False and len(result["missing"]) == 7

    def test_not_configured_is_explained(self):
        model = mock.Mock(is_configured=False)
        with self.assertRaisesMessage(limits.LimitsUnavailable, "не настроено"):
            limits.extract_limits(card("card_puls"), "ПУЛЬС", client=model)
