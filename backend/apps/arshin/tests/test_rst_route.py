"""Госреестр через MCP-сервер РСТ, запасной путь напрямую и индикатор связи.

Ответы MCP-сервера — настоящие, сняты с RSTMetrology_MCP 09.11.2026
(fixtures/rst_mcp_samples.json): поиск «СВК», «манометр», по номеру, пустой поиск, карточка
СВКО и ошибка инструмента. Сеть в тестах не трогается: подменяется только сессия MCP и
``requests.get`` прямого пути.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.arshin import rst_mcp
from apps.arshin import status as fif_status
from apps.arshin.fif import FifClient, FifType, FifUnavailable, rank
from apps.assistant.mcp_client import McpError
from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import User

FIXTURES = Path(__file__).parent / "fixtures"
MCP = json.loads((FIXTURES / "rst_mcp_samples.json").read_text(encoding="utf-8"))
DIRECT = json.loads((FIXTURES / "fif_samples.json").read_text(encoding="utf-8"))
RST = {"RST_MCP_URL": "http://rst.test/mcp", "RST_MCP_TOKEN": "t", "FIF_TRANSPORT": "auto"}


def mcp_reply(key: str) -> dict:
    return MCP[key]["result"]


class FakeMcp:
    """Подмена MCP-сессии: отвечает снятыми ответами по (инструмент, аргумент поиска/id)."""

    def __init__(self, answers: dict | None = None, *, fail: list | None = None):
        self.answers = answers or {}
        self.fail = list(fail or [])  # исключения, которые бросить на очередных вызовах
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, timeout):  # rst_mcp._session(timeout)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.fail:
            raise self.fail.pop(0)
        key = (name, arguments.get("search") or arguments.get("id"))
        if key not in self.answers:
            raise AssertionError(f"неожиданный вызов MCP: {key}")
        return mcp_reply(self.answers[key])


def direct_ok(payload):
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class Base(SimpleTestCase):
    def setUp(self):
        cache.clear()
        fif_status._local.clear()
        sleep = mock.patch("apps.arshin.fif.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def use(self, fake: FakeMcp) -> FakeMcp:
        patcher = mock.patch.object(rst_mcp, "_session", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


@override_settings(**RST)
class McpSearchTests(Base):
    def test_search_goes_through_mcp_and_parses_live_answer(self):
        fake = self.use(FakeMcp({("search_types", "СВК"): "search_svk"}))
        with mock.patch("apps.arshin.fif.requests.get") as get:
            found = FifClient().search_types("СВК", limit=10)
        get.assert_not_called()  # MCP ответил — напрямую в ФГИС не ходим
        assert fake.calls == [("search_types", {"search": "СВК", "rows": 50})]
        assert len(found) == 10
        assert found[0].notation == "СВК" and found[0].uuid and found[0].manufacturer
        assert all(item.registry_number for item in found)

    def test_modification_query_finds_the_base_type_first(self):
        """«СВК 15-3-2» в реестре нет — есть «СВК»: он и должен быть сверху, а не «ИНТЕГРА-КДД-СВК»."""
        self.use(FakeMcp({("search_types", "СВК"): "search_svk"}))
        found = FifClient().search_types("СВК 15-3-2", limit=5)
        assert found[0].notation in ("СВК-15", "СВК", "СВК 15-1,5"), found[0]
        assert "ИНТЕГРА" not in found[0].notation

    def test_word_search_puts_matching_titles_first(self):
        """ФГИС отдаёт по номеру в реестре; сверху должны быть манометры, а не тонометры."""
        self.use(FakeMcp({("search_types", "манометр"): "search_manometr"}))
        found = FifClient().search_types("манометр", limit=5)
        assert all(item.title.lower().startswith("манометр") for item in found), [i.title for i in found]

    def test_registry_number_is_found_exactly(self):
        self.use(FakeMcp({("search_types", "98511-26"): "search_number"}))
        found = FifClient().search_types("98511-26")
        assert found[0].registry_number == "98511-26" and found[0].notation == "СВКО"

    def test_nothing_found_is_empty_not_error(self):
        self.use(FakeMcp({("search_types", "ЪЪЪЖЖЖ"): "search_nothing"}))
        assert FifClient().search_types("ЪЪЪЖЖЖ") == []
        assert fif_status.last("mcp")["ok"] is True

    def test_card_through_mcp(self):
        fake = self.use(FakeMcp({("get_type", "2b5223e6-d4f0-dd03-b0cd-2dcc8749bba1"): "card_svko"}))
        card = FifClient().get_type("2b5223e6-d4f0-dd03-b0cd-2dcc8749bba1")
        assert card.registry_number == "98511-26"
        assert card.title == "Счетчики воды крыльчатые одноструйные"
        assert [m["months"] for m in card.mpi] == [72]
        assert {d.kind for d in card.documents} == {"meth", "spec"}
        assert "СВКО" in card.variants[0]
        FifClient().get_type("2b5223e6-d4f0-dd03-b0cd-2dcc8749bba1")
        assert len(fake.calls) == 1, "карточка кэшируется"

    def test_same_search_is_cached(self):
        fake = self.use(FakeMcp({("search_types", "СВК"): "search_svk"}))
        FifClient().search_types("СВК")
        FifClient().search_types("СВК")
        assert len(fake.calls) == 1

    def test_broken_cache_does_not_break_search(self):
        """Локально без Redis кэш падает — поиск должен работать (так «поиск не работал»)."""
        self.use(FakeMcp({("search_types", "СВК"): "search_svk"}))
        with mock.patch("apps.arshin.status.cache.get", side_effect=ConnectionError("redis")), \
                mock.patch("apps.arshin.status.cache.set", side_effect=ConnectionError("redis")):
            found = FifClient().search_types("СВК")
            with mock.patch("apps.arshin.fif.requests.get") as get:
                assert found and fif_status.snapshot()["via"] == "mcp"
            get.assert_not_called()  # основной путь на связи — запасной не дёргаем


@override_settings(**RST)
class FallbackTests(Base):
    def test_tool_error_falls_back_to_direct_and_is_shown(self):
        self.use(FakeMcp({("search_types", "СВК"): "card_missing_arg"}))  # isError: true
        with mock.patch("apps.arshin.fif.requests.get", return_value=direct_ok(DIRECT["search_svk"])) as get:
            found = FifClient().search_types("СВК")
        assert get.call_args.kwargs["params"]["search"] == "*СВК*"
        assert found and found[0].notation.startswith("СВК")
        assert fif_status.last("mcp")["ok"] is False
        assert "не задан обязательный аргумент" in fif_status.last("mcp")["error"]
        assert fif_status.last("direct")["ok"] is True

    def test_both_down_explains_each_route(self):
        self.use(FakeMcp(fail=[McpError("MCP-сервер недоступен: connection refused")] * 2))
        with mock.patch("apps.arshin.fif.requests.get", side_effect=requests.ConnectTimeout("x")):
            with pytest.raises(FifUnavailable) as exc:
                FifClient().search_types("СВК")
        text = str(exc.value)
        assert "MCP-сервер РСТ" in text and "ФГИС напрямую" in text
        assert "только российским адресам" in text

    def test_dropped_answer_is_retried_once(self):
        fake = self.use(FakeMcp({("search_types", "СВК"): "search_svk"},
                                fail=[McpError("Response ended prematurely")]))
        assert FifClient().search_types("СВК")
        assert len(fake.calls) == 2

    def test_bad_token_is_not_retried(self):
        fake = self.use(FakeMcp(fail=[McpError("MCP-сервер отказал в доступе (401) — проверьте токен")] * 2))
        with mock.patch("apps.arshin.fif.requests.get", return_value=direct_ok(DIRECT["search_svk"])):
            FifClient().search_types("СВК")
        assert len(fake.calls) == 1
        assert "401" in fif_status.last("mcp")["error"]

    @override_settings(FIF_TRANSPORT="direct")
    def test_direct_only_never_calls_mcp(self):
        fake = self.use(FakeMcp())
        with mock.patch("apps.arshin.fif.requests.get", return_value=direct_ok(DIRECT["search_svk"])):
            FifClient().search_types("СВК")
        assert fake.calls == [] and fif_status.routes() == ["direct"]

    @override_settings(RST_MCP_URL="")
    def test_without_mcp_only_direct(self):
        assert fif_status.routes() == ["direct"]


class RankTests(SimpleTestCase):
    def test_order(self):
        items = [
            FifType(registry_number="1-01", notation="«ИНТЕГРА-КДД-СВК»", title="Комплексы"),
            FifType(registry_number="2-02", notation="НОРМА СВК", title="Счетчики воды"),
            FifType(registry_number="3-03", notation="СВК", title="Счетчики воды крыльчатые"),
            FifType(registry_number="4-04", notation="СВК-15У", title="Счетчики воды"),
        ]
        assert [i.registry_number for i in rank(items, "СВК")][:2] == ["3-03", "4-04"]
        assert rank(items, "СВК 15")[0].registry_number == "4-04"
        assert rank(items, "2-02")[0].registry_number == "2-02"


@override_settings(**RST)
class StatusApiTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("v@ei.test", "pw"))

    def test_needs_login(self):
        assert APIClient().get(reverse("fif_status")).status_code == 401

    def test_probe_reports_each_route(self):
        self.use(FakeMcp({("search_types", "16078-05"): "search_number"}))
        with mock.patch("apps.arshin.fif.requests.get", side_effect=requests.ConnectTimeout("x")):
            body = self.client.get(reverse("fif_status"), {"probe": 1}).data
        assert body["ok"] is True and body["via"] == "mcp" and body["mode"] == "auto"
        mcp, direct = body["routes"]
        assert (mcp["key"], mcp["ok"]) == ("mcp", True) and isinstance(mcp["ms"], int)
        assert (direct["key"], direct["ok"]) == ("direct", False) and "не ответила" in direct["error"]

    def test_fresh_marks_are_not_probed_again(self):
        fif_status.record("mcp", True, ms=300)
        fif_status.record("direct", False, error="нет соединения")
        fake = self.use(FakeMcp())
        with mock.patch("apps.arshin.fif.requests.get") as get:
            body = self.client.get(reverse("fif_status")).data
        assert fake.calls == [] and not get.called
        assert body["via"] == "mcp"

    def test_all_down(self):
        self.use(FakeMcp(fail=[McpError("недоступен")] * 4))
        with mock.patch("apps.arshin.fif.requests.get", side_effect=requests.ConnectionError("x")):
            body = self.client.get(reverse("fif_status"), {"probe": 1}).data
        assert body["ok"] is False and body["via"] is None


@override_settings(**RST)
class SuggestThroughMcpTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("v@ei.test", "pw"))
        family = MeasurementFamily.objects.create(code="water", name="Счётчики воды")
        SiType.objects.create(family=family, registry_number="95304-25", name="СВК", manufacturer="X")

    def test_suggest_lists_local_then_registry(self):
        self.use(FakeMcp({("search_types", "СВК"): "search_svk"}))
        body = self.client.get(reverse("si_type_suggest"), {"q": "СВК"}).data
        assert body["fif"]["ok"] is True
        sources = [r["source"] for r in body["results"]]
        assert sources[0] == "local" and "fif" in sources
        assert all(r["fif_uuid"] for r in body["results"] if r["source"] == "fif")

    def test_registry_down_still_answers_with_local_and_reason(self):
        self.use(FakeMcp(fail=[McpError("недоступен")] * 2))
        with mock.patch("apps.arshin.fif.requests.get", side_effect=requests.ConnectionError("x")):
            response = self.client.get(reverse("si_type_suggest"), {"q": "СВК"})
        assert response.status_code == 200
        assert [r["source"] for r in response.data["results"]] == ["local"]
        assert response.data["fif"]["ok"] is False and "MCP-сервер РСТ" in response.data["warning"]
