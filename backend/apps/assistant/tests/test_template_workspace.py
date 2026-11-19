"""«Создать шаблон протокола поверки»: чат на тип СИ, живой черновик, «Продолжить»."""

from __future__ import annotations

import copy
import json
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.arshin.fif import FifCard, FifDocumentRef
from apps.assistant import llm, template_workspace
from apps.assistant.models import Conversation, Message, TemplateWorkspace
from apps.catalog.models import MeasurementFamily, SiType, StandardKit
from apps.core.models import Role, User
from apps.verification import templates
from apps.verification.models import VerificationTemplate
from apps.verification.tests.test_templates import MANOMETER

MODEL = {"AGENT_SERVICE_TOKEN": "", "OPENROUTER_API_KEY": "k", "ASSISTANT_DEFAULT_MODEL": "openrouter:test/model"}


class FakeModel:
    """«Модель»: на первом шаге сохраняет черновик инструментом рабочего места, затем отвечает текстом."""

    def __init__(self, save: dict):
        self.save = save
        self.calls = []

    def __call__(self, model, messages, tools=None, **kw):
        self.calls.append({"system": messages[0]["content"], "tools": [t["function"]["name"] for t in tools or []]})
        if len(self.calls) % 2 == 1 and self.save is not None:
            return llm.Completion(tool_calls=[llm.ToolCall(f"c{len(self.calls)}", "ei_workspace_template_save",
                                                           json.dumps(self.save))])
        return llm.Completion(text="Шаблон по МИ 2124-90: операции, точки 0–100 %, наборы из перечня.")


@override_settings(**MODEL)
class TemplateWorkspaceTests(TestCase):
    def setUp(self):
        self.family = MeasurementFamily.objects.create(code="manometers", name="Манометры")
        self.water = MeasurementFamily.objects.create(code="water", name="Счётчики воды", calculator_key="water_meter")
        self.kit = StandardKit.objects.create(section="Перечень для поверки манометров", scope="до 2,5 МПа")
        self.kit.families.set([self.family])
        self.si_type = SiType.objects.create(family=self.family, registry_number="39153-08", name="ДМ 02",
                                             fif_uuid="uuid-1")
        self.verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.metrologist = User.objects.create_user("m@ei.test", "pw", role=Role.METROLOGIST)
        self.save = {"title": "ДМ 02 — МИ 2124-90", "plan": copy.deepcopy(MANOMETER), "kits": [self.kit.pk],
                     "sources": ["МИ 2124-90, пп. 5.1–5.4"]}

    def client_for(self, user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    def open(self, user, fake, **body):
        with mock.patch("apps.assistant.llm.chat", fake), mock.patch("apps.assistant.agent.all_tools", lambda: ({}, [])), \
                mock.patch("apps.assistant.llm.model_info", lambda model: {}):
            return self.client_for(user).post(reverse("assistant_workspaces"), body or {"si_type": self.si_type.pk},
                                              format="json")

    def say(self, workspace, user, fake, text="Добавь проверку герметичности"):
        with mock.patch("apps.assistant.llm.chat", fake), mock.patch("apps.assistant.agent.all_tools", lambda: ({}, [])), \
                mock.patch("apps.assistant.llm.model_info", lambda model: {}):
            return self.client_for(user).post(reverse("assistant_send", args=[workspace.conversation_id]),
                                              {"content": text, "model": "openrouter:test/model"}, format="json")

    def test_open_gives_agent_the_type_and_saves_inactive_draft(self):
        fake = FakeModel(self.save)
        response = self.open(self.verifier, fake)
        assert response.status_code == 201, response.data
        workspace = TemplateWorkspace.objects.get(pk=response.data["id"])
        # Агент получил своё задание, раздел о типе и инструменты черновика.
        first = workspace.conversation.messages.filter(role=Message.Role.USER).first()
        assert "ДМ 02" in first.content and "методику" in first.content
        assert "Создать шаблон протокола поверки" in fake.calls[0]["system"] and "39153-08" in fake.calls[0]["system"]
        assert {"ei_workspace_template_save", "ei_workspace_template_get"} <= set(fake.calls[0]["tools"])
        # Черновик есть, но выключен: протоколы по нему пока не идут.
        draft = workspace.draft
        assert draft and not draft.is_active and draft.si_type == self.si_type and workspace.revision == 1
        assert list(draft.kits.all()) == [self.kit]
        assert templates.template_for(self.si_type) is None
        # Чат режима не в общем списке чатов, а повторное открытие — то же рабочее место.
        listed = self.client_for(self.verifier).get(reverse("assistant_conversations")).data
        assert workspace.conversation_id not in [c["id"] for c in listed]
        again = self.open(self.verifier, FakeModel(None))
        assert again.status_code == 200 and again.data["id"] == workspace.pk
        detail = self.client_for(self.verifier).get(reverse("assistant_workspace", args=[workspace.pk])).data
        assert detail["draft"]["operations"] and detail["status"] == "draft" and detail["published"] is None

    def test_preview_of_draft(self):
        workspace = TemplateWorkspace.objects.get(pk=self.open(self.verifier, FakeModel(self.save)).data["id"])
        response = self.client_for(self.verifier).get(
            reverse("assistant_workspace_preview", args=[workspace.pk, "docx"]))
        assert response.status_code == 200 and response.content[:2] == b"PK"
        # Чужое рабочее место не открывается.
        other = self.client_for(self.metrologist).get(reverse("assistant_workspace", args=[workspace.pk]))
        assert other.status_code == 404

    def test_publish_then_new_draft_keeps_published_until_next_publish(self):
        workspace = TemplateWorkspace.objects.get(pk=self.open(self.verifier, FakeModel(self.save)).data["id"])
        response = self.client_for(self.verifier).post(reverse("assistant_workspace_publish", args=[workspace.pk]))
        assert response.status_code == 200, response.data
        assert response.data["status"] == "published"
        first = templates.template_for(self.si_type)
        assert first and first.is_active and first.title == "ДМ 02 — МИ 2124-90"
        assert first.needs_review  # от поверителя — ждёт метролога, протокол не подпишется
        # Правка после «Продолжить» — в новый черновик, действующий шаблон не меняется.
        changed = copy.deepcopy(self.save)
        changed["plan"]["operations"] = [*changed["plan"].get("operations", []),
                                         {"key": "tight", "label": "Проверка герметичности", "source": "п. 5.3"}]
        assert self.say(workspace, self.verifier, FakeModel(changed)).status_code in (200, 201)
        workspace.refresh_from_db()
        assert workspace.draft and workspace.draft.pk != first.pk and not workspace.draft.is_active
        assert workspace.draft.title.endswith("новая редакция")
        assert templates.template_for(self.si_type).pk == first.pk
        # Повторная «Продолжить»: новая редакция действует под тем же именем, старая выключена.
        self.client_for(self.verifier).post(reverse("assistant_workspace_publish", args=[workspace.pk]))
        current = templates.template_for(self.si_type)
        first.refresh_from_db()
        assert current.pk != first.pk and current.title == "ДМ 02 — МИ 2124-90"
        assert not first.is_active and "ред. до" in first.title
        assert "Проверка герметичности" in [op["label"] for op in current.plan["operations"]]
        assert workspace.conversation.messages.filter(role=Message.Role.EVENT, content__contains="Продолжить").exists()

    def test_metrologist_publishes_reviewed(self):
        workspace = TemplateWorkspace.objects.get(pk=self.open(self.metrologist, FakeModel(self.save)).data["id"])
        self.client_for(self.metrologist).post(reverse("assistant_workspace_publish", args=[workspace.pk]))
        assert not templates.template_for(self.si_type).needs_review

    def test_publish_without_draft_is_400(self):
        workspace = TemplateWorkspace.objects.get(pk=self.open(self.verifier, FakeModel(None)).data["id"])
        response = self.client_for(self.verifier).post(reverse("assistant_workspace_publish", args=[workspace.pk]))
        assert response.status_code == 400 and "Черновика" in response.data["detail"]

    def test_type_wrongly_in_water_family_is_fixed_here(self):
        self.si_type.family = self.water
        self.si_type.save()
        # Тип уже проверен метрологом — вид СИ поверитель не меняет.
        workspace0 = TemplateWorkspace.objects.get(pk=self.open(self.verifier, FakeModel(None)).data["id"])
        response = self.client_for(self.verifier).patch(reverse("assistant_workspace", args=[workspace0.pk]),
                                                        {"family": self.family.pk}, format="json")
        assert response.status_code == 403
        workspace0.conversation.delete()
        self.si_type.needs_review = True
        self.si_type.save()
        workspace = TemplateWorkspace.objects.get(pk=self.open(self.verifier, FakeModel(self.save)).data["id"])
        assert workspace.draft is None  # агент получил отказ: «поменять Вид СИ»
        tool = workspace.conversation.messages.filter(role=Message.Role.TOOL).first()
        assert "Вид СИ" in tool.content
        response = self.client_for(self.verifier).patch(reverse("assistant_workspace", args=[workspace.pk]),
                                                        {"family": self.family.pk}, format="json")
        assert response.status_code == 200 and not response.data["si_type"]["is_water"]
        self.say(workspace, self.verifier, FakeModel(self.save), "Вид поправил, сохрани шаблон")
        workspace.refresh_from_db()
        assert workspace.draft and workspace.draft.family == self.family

    def test_existing_active_template_is_the_starting_point(self):
        plan = templates.clean_template(copy.deepcopy(MANOMETER))
        old = VerificationTemplate.objects.create(title="ДМ 02 — МИ 2124-90", family=self.family,
                                                  si_type=self.si_type, plan=plan, is_active=True)
        fake = FakeModel(None)
        workspace = TemplateWorkspace.objects.get(pk=self.open(self.verifier, fake).data["id"])
        assert workspace.published == old and workspace.status == "published"
        assert "уже есть шаблон" in workspace.conversation.messages.first().content

    def test_new_type_from_fif_card(self):
        card = FifCard(uuid="uuid-9", registry_number="12345-19", title="Термометры сопротивления",
                       notation="ТС-1, ТС-2", manufacturer="Завод", valid_to=None,
                       mpi=[{"parameter": "", "months": 24, "valid_from": None}],
                       documents=[FifDocumentRef("meth", "МП 12345-19", "https://fgis.gost.ru/fundmetrology/doc/d-1")])
        with mock.patch("apps.arshin.fif.FifClient.get_type", return_value=card):
            response = self.open(self.verifier, FakeModel(None), fif_uuid="uuid-9", name="ТС-1")
        assert response.status_code == 201, response.data
        si_type = SiType.objects.get(fif_uuid="uuid-9")
        assert si_type.name == "ТС-1" and si_type.family.code == "other"  # не счётчик воды
        assert si_type.verification_interval_months == 24 and si_type.needs_review
        assert [d["title"] for d in response.data["documents"]] == ["МП 12345-19"]
        assert not si_type.template_jobs.exists()  # фоновая работа не ставится — шаблон делается в чате


class WorkspaceToolGuardTests(TestCase):
    def test_tools_only_in_workspace_chat(self):
        user = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        conversation = Conversation.objects.create(user=user)
        assert template_workspace.tools_for(conversation) == []
        assert template_workspace.prompt_for(conversation) == ""
