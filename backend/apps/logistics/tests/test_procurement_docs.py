"""Режим «Закупки»: текст документов, поиск, выжимка, план заходов — API и инструменты ассистента."""

from __future__ import annotations

import datetime as dt
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.assistant import actions, procurement_tools
from apps.assistant.models import Conversation, PendingAction
from apps.assistant.tools import ToolContext, ToolFailure, all_tools
from apps.core.models import Employee, Role, User
from apps.logistics.models import Procurement, ProcurementDocument, TextStatus, Trip, TripKind
from apps.verification.models import Client, ClientKind

TZ_PARAGRAPHS = [
    "Техническое задание на оказание услуг по поверке средств измерений",
    "1. Требования к исполнителю: аккредитация в национальной системе аккредитации на поверку СИ.",
    "2. Допуск на объект — по пропускам, заявка на пропуск подаётся за 5 рабочих дней.",
    "3. За просрочку исполнитель уплачивает неустойку 0,1 % цены контракта за каждый день.",
]


def docx_bytes(paragraphs: list[str]) -> bytes:
    import docx

    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def xlsx_bytes(rows: list[list]) -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


SUMMARY = {
    "requirements": ["Аккредитация на поверку СИ"],
    "instruments": [
        {"name": "Манометр", "type": "МП-100", "quantity": 30, "address": "г Киров, ул Ленина, 1", "source": "ТЗ, прил. 1"},
        {"name": "Счётчик воды", "quantity": 50, "address": "г Киров, ул Мира, 5"},
        {"name": "Термометр", "quantity": 10, "address": "г Киров, ул Ленина, 1"},
    ],
    "stages": [{"title": "Поверка", "date_from": "2026-12-01", "date_to": "2026-12-20"}],
    "access": [{"text": "Пропуск за 5 рабочих дней", "source": "ТЗ, п. 2"}],
    "contacts": [{"name": "Иванов И. И.", "position": "главный метролог", "phone": "+7 912 000-00-00"}],
    "penalties": ["0,1 % в день"],
    "deadline": "2026-12-25",
}


@override_settings(ASSISTANT_RUN_MODE="sync")
class ProcurementDocsTests(TestCase):
    def setUp(self) -> None:
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.settings_media = override_settings(MEDIA_ROOT=self.media, SENDFILE_ROOT=self.media)
        self.settings_media.enable()
        self.addCleanup(self.settings_media.disable)
        self.boss = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        self.lead = Employee.objects.create(tab_number="01", full_name="Менеджер М. М.", user=self.boss)
        self.verifier_user = User.objects.create_user(email="v@ei.test", password="x", role=Role.VERIFIER)
        Employee.objects.create(tab_number="05", full_name="Поверитель П. П.", user=self.verifier_user)
        self.api = APIClient()
        self.api.force_authenticate(self.boss)
        customer = Client.objects.create(kind=ClientKind.ORG, name="МУП «Водоканал»", inn="4345000249")
        self.procurement = Procurement.objects.create(number="0340200003326001234", title="Поверка СИ",
                                                      customer=customer, deadline=dt.date(2026, 12, 25))

    def upload(self, name: str, data: bytes, kind: str = "notice") -> dict:
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(f"/api/procurements/{self.procurement.pk}/documents/",
                                     {"file": SimpleUploadedFile(name, data), "kind": kind}, format="multipart")
        assert response.status_code == 201, response.data
        return response.data

    def test_text_is_extracted_and_searchable(self):
        doc = self.upload("ТЗ.docx", docx_bytes(TZ_PARAGRAPHS))
        stored = ProcurementDocument.objects.get(pk=doc["id"])
        assert stored.text_status == TextStatus.DONE and "неустойку" in stored.text
        self.upload("Перечень.xlsx", xlsx_bytes([["Наименование", "Кол-во"], ["Манометр МП-100", 30]]), kind="other")

        listing = self.api.get(f"/api/procurements/{self.procurement.pk}/documents/").data
        assert {row["text_status"] for row in listing} == {"done"} and all(row["text_chars"] > 10 for row in listing)

        found = self.api.get(f"/api/procurements/{self.procurement.pk}/search/", {"q": "пропуск"}).data
        assert [r["document_id"] for r in found["results"]] == [doc["id"]]
        assert "заявка на пропуск" in found["results"][0]["snippets"][0]["text"]
        found = self.api.get(f"/api/procurements/{self.procurement.pk}/search/", {"q": "Манометр"}).data
        assert found["results"][0]["title"] == "Перечень"

        text = self.api.get(f"/api/procurement-documents/{doc['id']}/text/", {"max_chars": 1000}).data
        assert text["total"] == len(stored.text) and text["text"].startswith("Техническое задание")

    def test_unreadable_file_is_marked_not_lost(self):
        doc = self.upload("скан.pdf", b"%PDF-1.4 broken")
        stored = ProcurementDocument.objects.get(pk=doc["id"])
        assert stored.text_status in (TextStatus.FAILED, TextStatus.EMPTY) and stored.text_error
        again = self.api.post(f"/api/procurement-documents/{doc['id']}/extract/")
        assert again.status_code == 200 and again.data["text_status"] == stored.text_status

    def test_summary_saved_with_author_and_validated(self):
        url = f"/api/procurements/{self.procurement.pk}/summary/"
        assert self.api.put(url, {"summary": {"instruments": [{"quantity": 3}]}}, format="json").status_code == 400
        saved = self.api.put(url, {"summary": SUMMARY, "sources": ["ТЗ"]}, format="json")
        assert saved.status_code == 200, saved.data
        assert saved.data["saved_by"] == "Менеджер М. М." and saved.data["saved_at"]
        assert saved.data["summary"]["requirements"] == [{"text": "Аккредитация на поверку СИ", "source": ""}]
        assert self.api.get(f"/api/procurements/{self.procurement.pk}/").data["summary"]["via_assistant"] is False

        verifier = APIClient()
        verifier.force_authenticate(self.verifier_user)
        assert verifier.get(url).status_code == 404  # закупку не видит
        assert verifier.put(url, {"summary": SUMMARY}, format="json").status_code == 403

    def test_visit_plan_groups_addresses_and_creates_trips(self):
        self.api.put(f"/api/procurements/{self.procurement.pk}/summary/", {"summary": SUMMARY}, format="json")
        url = f"/api/procurements/{self.procurement.pk}/visit-plan/"
        plan = self.api.post(url, {"per_day": 40, "days_per_trip": 1, "start": "2026-12-07"}, format="json").data
        assert plan["instruments_total"] == 90 and plan["addresses"] == 2
        assert [t["quantity"] for t in plan["trips"]] == [40, 40, 10]
        assert plan["trips"][0]["days"][0]["stops"][0]["address"] == "г Киров, ул Ленина, 1"

        created = self.api.post(url, {"per_day": 100, "days_per_trip": 5, "start": "2026-12-07", "apply": True},
                                format="json")
        assert created.status_code == 201, created.data
        trip = Trip.objects.get(pk=created.data["created"][0]["id"])
        assert trip.kind == TripKind.PROCUREMENT and trip.procurement == self.procurement and trip.lead == self.lead
        assert sorted(s.planned_count for s in trip.stops.all()) == [40, 50]


@override_settings(ASSISTANT_RUN_MODE="sync")
class ProcurementToolsTests(TestCase):
    def setUp(self) -> None:
        self.boss = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        self.lead = Employee.objects.create(tab_number="01", full_name="Менеджер М. М.", user=self.boss)
        self.verifier = User.objects.create_user(email="v@ei.test", password="x", role=Role.VERIFIER)
        customer = Client.objects.create(kind=ClientKind.ORG, name="МУП «Водоканал»")
        self.procurement = Procurement.objects.create(number="77", title="Поверка", customer=customer)
        ProcurementDocument.objects.create(procurement=self.procurement, title="ТЗ", file="procurements/tz.docx",
                                           text="\n".join(TZ_PARAGRAPHS), text_status=TextStatus.DONE)

    def ctx(self, user) -> ToolContext:
        return ToolContext(user=user, conversation=Conversation.objects.create(user=user))

    def confirm(self, result) -> PendingAction:
        action = PendingAction.objects.get(pk=result.ui["action"])
        return actions.decide(action, action.conversation.user, confirm=True)

    def test_registered(self):
        tools, _ = all_tools(with_mcp=False)
        for name in ("ei_procurement_documents", "ei_procurement_search", "ei_procurement_summary_propose",
                     "ei_procurement_visits_propose"):
            assert name in tools

    def test_read_and_search(self):
        ctx = self.ctx(self.boss)
        listing = procurement_tools.ei_procurement_documents(ctx, {"procurement_id": self.procurement.pk})
        assert "ТЗ" in listing.content
        doc = self.procurement.documents.get()
        text = procurement_tools.ei_procurement_documents(ctx, {"procurement_id": self.procurement.pk,
                                                                "document_id": doc.pk})
        assert "неустойку" in text.content
        hits = procurement_tools.ei_procurement_search(ctx, {"procurement_id": self.procurement.pk, "query": "неустойк"})
        assert "0,1 %" in hits.content
        with self.assertRaises(ToolFailure):
            procurement_tools.ei_procurement_documents(self.ctx(self.verifier), {"procurement_id": self.procurement.pk})

    def test_summary_card_then_confirm(self):
        with self.assertRaises(ToolFailure):  # без источников
            procurement_tools.ei_procurement_summary_propose(
                self.ctx(self.boss), {"procurement_id": self.procurement.pk, "summary": SUMMARY})
        result = procurement_tools.ei_procurement_summary_propose(
            self.ctx(self.boss), {"procurement_id": self.procurement.pk, "summary": SUMMARY, "sources": ["ТЗ, п. 1–3"]})
        assert self.confirm(result).status == PendingAction.Status.DONE
        self.procurement.refresh_from_db()
        assert self.procurement.summary_via_assistant and self.procurement.summary_by == self.boss
        assert len(self.procurement.summary["instruments"]) == 3 and self.procurement.summary_sources == ["ТЗ, п. 1–3"]

    def test_visits_card_creates_trips(self):
        from apps.logistics import procurement_docs

        procurement_docs.save_summary(self.procurement, self.boss, SUMMARY)
        result = procurement_tools.ei_procurement_visits_propose(
            self.ctx(self.boss), {"procurement_id": self.procurement.pk, "per_day": 100, "start": "2026-12-07"})
        action = self.confirm(result)
        assert action.status == PendingAction.Status.DONE, action.error
        assert Trip.objects.filter(procurement=self.procurement).count() == 1
        with self.assertRaises(ToolFailure):
            procurement_tools.ei_procurement_visits_propose(self.ctx(self.verifier), {"procurement_id": self.procurement.pk})
