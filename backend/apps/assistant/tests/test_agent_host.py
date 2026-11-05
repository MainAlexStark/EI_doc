"""EI_doc как MCP-сервер хоста для агента на agentkit (host.py) и вход во фрейм чата (agent_link.py)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.assistant.models import AssistantSettings, Conversation, MemoryItem, PendingAction, Scope
from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Role, User

LIMITS = {
    "q_min": 0.03, "q_transition_a": 0.15, "q_transition_b": 0.12, "q_nominal": 1.5,
    "q_max": 3, "error_below_transition": 5, "error_above_transition": 2,
}
TOKEN = "host-token"
MEDIA = tempfile.mkdtemp(prefix="ei-agent-host-")


@override_settings(AGENT_HOST_TOKEN=TOKEN, MEDIA_ROOT=MEDIA)
class HostTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.family = MeasurementFamily.objects.create(code="water-meter", name="Счётчики воды",
                                                       calculator_key="water_meter")
        self.verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.observer = User.objects.create_user("o@ei.test", "pw", role=Role.OBSERVER)
        self.n = 0

    def rpc(self, method, params=None, *, user=None, session="s_1", token=TOKEN):
        self.n += 1
        headers = {"HTTP_X_AGENT_USER_ID": str((user or self.verifier).pk), "HTTP_X_AGENT_SESSION_ID": session}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        r = self.client.post(reverse("assistant_agent_mcp"), json.dumps({"jsonrpc": "2.0", "id": self.n,
                                                                        "method": method, "params": params or {}}),
                             content_type="application/json", **headers)
        return r

    def result(self, *a, **kw):
        r = self.rpc(*a, **kw)
        assert r.status_code == 200, r.content
        body = r.json()
        assert "error" not in body, body
        return body["result"]

    def type_args(self, **over):
        args = {"registry_number": "77777-21", "name": "Счётчик ТЕСТ-15", "manufacturer": "ООО Завод",
                "verification_interval_months": 72, "family": self.family.pk, "limits": LIMITS,
                "sources": ["ФГИС: get_type 77777-21"]}
        args.update(over)
        return args

    def test_needs_host_token(self):
        assert self.rpc("tools/list", token="").status_code == 401
        assert self.rpc("tools/list", token="чужой").status_code == 401
        unknown = self.client.post(reverse("assistant_agent_mcp"), json.dumps({"jsonrpc": "2.0", "id": 1,
                                                                              "method": "tools/list"}),
                                   content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
                                   HTTP_X_AGENT_USER_ID="999999").json()
        assert "не найден" in unknown["error"]["message"]

    def test_instructions_have_company_context_not_personal_memory(self):
        s = AssistantSettings.current()
        s.extra_instructions = "Протоколы по счётчикам — по МИ 1592-2015."
        s.save()
        MemoryItem.objects.create(scope=Scope.COMPANY, text="Эталоны — только из справочника", created_by=self.verifier)
        MemoryItem.objects.create(scope=Scope.PERSONAL, user=self.verifier, text="Мои районы — Слободской",
                                  created_by=self.verifier)
        text = self.result("initialize", {"protocolVersion": "2025-06-18"})["instructions"]
        assert "МИ 1592-2015" in text and "Эталоны — только из справочника" in text
        assert "Слободской" not in text  # личная память — в агенте
        assert "Поверитель" in text or "поверитель" in text.lower()

    def test_tool_list(self):
        tools = {t["name"]: t for t in self.result("tools/list")["tools"]}
        assert not {"memory_save", "memory_delete", "ui_show", "attachment_read", "web_fetch"} & set(tools)
        assert tools["ei_journal"]["annotations"]["readOnlyHint"] is True
        assert tools["ei_journal"]["title"] == "Смотрю журнал поверок"
        prop = tools["ei_si_type_propose"]
        assert prop["annotations"]["readOnlyHint"] is False and prop["_meta"]["agentkit/preview"] is True
        assert {"ei_availability_propose", "ei_company_memory_propose", "ei_company_memory_delete"} <= set(tools)

    def test_dry_run_builds_card_and_changes_nothing(self):
        res = self.result("tools/call", {"name": "ei_si_type_propose", "arguments": self.type_args(),
                                         "_meta": {"agentkit/dryRun": True}})
        view = dict(map(tuple, res["structuredContent"]["view"]))
        assert view["Действие"].startswith("Завести тип СИ: Счётчик ТЕСТ-15")
        assert view["Изготовитель"] == "ООО Завод" and "ФГИС" in view["Источники"]
        assert not PendingAction.objects.exists() and not SiType.objects.exists()

    def test_real_call_executes_as_user(self):
        res = self.result("tools/call", {"name": "ei_si_type_propose", "arguments": self.type_args()})
        assert not res["isError"] and "Выполнено" in res["content"][0]["text"]
        t = SiType.objects.get(registry_number="77777-21")
        assert t.created_by == self.verifier and t.needs_review  # заведён поверителем — ждёт метролога
        action = PendingAction.objects.get()
        assert action.status == "done" and action.conversation.external_id == "agentkit:s_1"
        # Такой тип уже есть — карточки не будет, агент получит ответ сразу.
        again = self.result("tools/call", {"name": "ei_si_type_propose", "arguments": self.type_args(),
                                           "_meta": {"agentkit/dryRun": True}})
        assert "structuredContent" not in again and "уже есть" in again["content"][0]["text"]

    def test_rights_are_checked_by_ei_doc(self):
        res = self.result("tools/call", {"name": "ei_si_type_propose", "arguments": self.type_args(),
                                         "_meta": {"agentkit/dryRun": True}}, user=self.observer)
        assert res["isError"] and "нет права" in res["content"][0]["text"]
        mem = self.result("tools/call", {"name": "ei_company_memory_propose", "arguments": {"text": "Новое правило"},
                                         "_meta": {"agentkit/dryRun": True}}, user=self.verifier)
        assert mem["isError"] and "метролог" in mem["content"][0]["text"] and not MemoryItem.objects.exists()

    def test_files_come_back_as_blobs(self):
        res = self.result("tools/call", {"name": "file_create", "arguments": {
            "formats": ["md"], "filename": "сводка", "content": "# Сводка\nВсё хорошо"}})
        blob = [c for c in res["content"] if c["type"] == "resource"][0]["resource"]
        assert blob["name"] == "сводка.md" and base64.b64decode(blob["blob"]).decode().startswith("# Сводка")

    def test_shadow_conversations_hidden_from_old_chat_list(self):
        self.result("tools/call", {"name": "ei_journal", "arguments": {}})
        assert Conversation.objects.filter(user=self.verifier, external_id="agentkit:s_1").exists()
        api = APIClient()
        api.force_authenticate(self.verifier)
        assert api.get(reverse("assistant_conversations")).data == []


class AgentLinkTests(TestCase):
    def setUp(self):
        self.head = User.objects.create_user("h@ei.test", "pw", role=Role.HEAD)
        self.api = APIClient()
        self.api.force_authenticate(self.head)

    def test_disabled_without_agent_url(self):
        assert self.api.get(reverse("assistant_agent")).data == {"enabled": False}

    @override_settings(AGENT_URL="https://agent.ei.test", AGENT_LOGIN_SECRET="s3cret")
    def test_token_for_frame(self):
        d = self.api.get(reverse("assistant_agent")).data
        assert d["enabled"] and d["url"] == "https://agent.ei.test/" and d["origin"] == "https://agent.ei.test"
        prefix, body, sig = d["token"].split(".")
        assert prefix == "akh1"
        want = base64.urlsafe_b64encode(hmac.new(b"s3cret", body.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        assert sig == want
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert data["sub"] == str(self.head.pk) and data["roles"] == ["admin", "head"]
