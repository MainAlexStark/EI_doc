"""Ассистент EI_doc на agentkit: промпт, MCP-сервер EI_doc и вход из EI_doc. Модель — FakeLLM.

Сервер EI_doc здесь поддельный — то, что он отдаёт, проверяют тесты EI_doc
(backend/apps/assistant/tests/test_agent_host.py); здесь — что агент с ним делает.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from agentkit import FakeLLM, ModelRouter
from agentkit.llm.router import RetryPolicy
from agentkit.server.app import build_app
from agentkit.server.auth import host_token
from agentkit.server.service import AgentService
from agentkit.server.settings import Settings
from fastapi.testclient import TestClient

from app.domain import build_domain

SECRET = "login-secret"


class FakeEI:
    """Поддельный MCP-сервер EI_doc: тип СИ с карточкой от EI_doc и протокол файлом."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer ei-token"
        msg = json.loads(req.content)
        if "id" not in msg:
            return httpx.Response(202)
        uid = req.headers.get("x-agent-user-id")
        if msg["method"] == "initialize":
            res = {"protocolVersion": "2025-06-18", "instructions": f"## Сейчас\nПользователь EI_doc №{uid}, поверитель."}
        elif msg["method"] == "tools/list":
            obj = {"type": "object", "properties": {"name": {"type": "string"}}}
            res = {"tools": [
                {"name": "ei_si_type_propose", "title": "Готовлю новый тип СИ", "description": "Завести тип",
                 "inputSchema": obj, "annotations": {"readOnlyHint": False}, "_meta": {"agentkit/preview": True}},
                {"name": "ei_journal", "title": "Смотрю журнал", "description": "Журнал", "inputSchema": obj,
                 "annotations": {"readOnlyHint": True}},
            ]}
        else:
            p = msg["params"]
            dry = bool((p.get("_meta") or {}).get("agentkit/dryRun"))
            self.calls.append({"user": uid, "name": p["name"], "dry": dry})
            if dry:
                res = {"content": [{"type": "text", "text": "ок"}],
                       "structuredContent": {"view": [["Действие", "Завести тип СИ: ВСКМ 90-15"]]}}
            else:
                res = {"content": [{"type": "text", "text": "Выполнено: тип СИ заведён (id 7)."}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": res})


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EI_MCP_URL", "http://ei.test/api/assistant/agent/mcp")
    monkeypatch.setenv("EI_MCP_TOKEN", "ei-token")
    monkeypatch.setenv("RST_MCP_URL", "")
    ei = FakeEI()
    real = httpx.AsyncClient.send

    async def send(self, request, **kw):  # только EI_doc — подделка, остальное как есть
        if request.url.host == "ei.test":
            return ei(request)
        return await real(self, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    fake = FakeLLM(default="ok")
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path}/a.db", scheduler=False,
                        auto_memory=False, host_login_secret=SECRET, embed_origins="https://ei.example.ru")
    router = ModelRouter(providers={"fake": fake}, roles={"main": "fake:m", "small": "fake:s"},
                         retry=RetryPolicy(attempts=1))
    with TestClient(build_app(AgentService(settings, build_domain(settings), router=router))) as c:
        c.fake, c.ei = fake, ei
        yield c


def auth(uid="12", roles=("verifier",)):
    return {"Authorization": f"Bearer {host_token(SECRET, uid, name='Иванов И.', roles=list(roles))}"}


def wait_idle(c, sid, headers):
    for _ in range(300):
        if not c.get(f"/api/sessions/{sid}", headers=headers).json()["running_run"]:
            return
        time.sleep(0.02)
    raise AssertionError("прогон не закончился")


def test_frame_and_login_from_ei_doc(client):
    page = client.get("/")
    assert "frame-ancestors 'self' https://ei.example.ru" == page.headers["content-security-policy"]
    me = client.get("/api/me", headers=auth()).json()
    assert me["id"] == "12" and me["name"] == "Иванов И."


def test_prompt_and_ei_tools(client):
    H = auth()
    client.fake.push("Готово")
    sid = client.post("/api/sessions", json={}, headers=H).json()["id"]
    client.post(f"/api/sessions/{sid}/messages", json={"text": "привет"}, headers=H)
    wait_idle(client, sid, H)
    req = client.fake.requests[-1]
    system = req.messages[0].text
    assert "ассистент-метролог ООО «Единица Измерения»" in system and "ошибки недопустимы" in system
    assert "Пользователь EI_doc №12" in system  # инструкции EI_doc на этого сотрудника
    names = {t["function"]["name"] for t in req.tools}
    assert {"ei_si_type_propose", "ei_journal"} <= names  # без приставки mcp__ei__ — как в промпте


def test_change_goes_through_ei_card(client):
    H = auth()
    client.fake.push({"tool_calls": [("ei_si_type_propose", {"name": "ВСКМ 90-15"})]}, "Тип заведён.")
    sid = client.post("/api/sessions", json={}, headers=H).json()["id"]
    client.post(f"/api/sessions/{sid}/messages", json={"text": "заведи тип"}, headers=H)
    wait_idle(client, sid, H)
    s = client.get(f"/api/sessions/{sid}", headers=H).json()
    assert s["approvals"][0]["view"] == [["Действие", "Завести тип СИ: ВСКМ 90-15"]]
    assert client.ei.calls == [{"user": "12", "name": "ei_si_type_propose", "dry": True}]
    client.post(f"/api/approvals/{s['approvals'][0]['id']}", json={"approve": True}, headers=H)
    wait_idle(client, sid, H)
    assert client.ei.calls[-1] == {"user": "12", "name": "ei_si_type_propose", "dry": False}
    assert client.get(f"/api/sessions/{sid}", headers=H).json()["messages"][-1]["content"] == "Тип заведён."
