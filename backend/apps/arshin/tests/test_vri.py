"""Прошлые поверки по заводскому номеру: сбор по годам, раскладка букв, карточка, API.

Ответы — настоящие, сняты с MCP-сервера РСТ 10.11.2026 (fixtures/vri_samples.json). Сеть не
трогается: подменяются ``rst_mcp.call`` и ``requests.get`` прямого пути.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.arshin import rst_mcp
from apps.arshin import status as fif_status
from apps.arshin.fif import FifUnavailable
from apps.arshin.vri import VriCard, VriClient, serial_key, serial_pattern, serial_variants
from apps.core.models import User
from apps.verification.tests import factories as f

SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "vri_samples.json").read_text(encoding="utf-8"))
RST = {"RST_MCP_URL": "http://rst.test/mcp", "RST_MCP_TOKEN": "t", "FIF_TRANSPORT": "auto", "FIF_VRI_YEARS_BACK": 3}
TODAY = dt.date(2026, 11, 10)
SERIAL = "С2674623"  # русская «С»
SERIAL_LATIN = "C2674623"
PATTERN = "?2674623"


class FakeRst:
    """Подмена ``rst_mcp.call``: в 2026 году под «?2674623» — наш счётчик и чужой «Z…», остальное пусто."""

    def __init__(self, *, fail_years: tuple[int, ...] = (), dead: bool = False):
        self.fail_years, self.dead = fail_years, dead
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool, arguments, *, timeout=None):
        self.calls.append((tool, dict(arguments)))
        if self.dead:
            raise rst_mcp.RstMcpError("MCP-сервер РСТ: нет связи")
        if tool == "get_verification":
            return copy.deepcopy(SAMPLES["card"])
        assert tool == "search_verifications"
        if arguments["year"] in self.fail_years:
            raise rst_mcp.RstMcpError("MCP-сервер РСТ: context deadline exceeded")
        assert arguments["mi_number"] == PATTERN, arguments
        if arguments["year"] != 2026:
            return copy.deepcopy(SAMPLES["year_empty"])
        answer = copy.deepcopy(SAMPLES["year_2026"])
        answer["items"].append({**answer["items"][0], "vri_id": "1-1", "mi_number": "Z2674623", "mit_number": "81336-21"})
        answer["items"].append({**answer["items"][0], "vri_id": "1-2", "mi_number": SERIAL_LATIN,
                                "mit_number": "25913-08", "mi_modification": "ТМ-510",
                                "verification_date": "2026-01-10"})
        return answer

    def searches(self) -> list[tuple[str, int]]:
        return [(a["mi_number"], a["year"]) for t, a in self.calls if t == "search_verifications"]


class Base:
    def setUp(self):
        super().setUp()
        cache.clear()
        fif_status._local.clear()
        sleep = mock.patch("apps.arshin.fif.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def use(self, fake: FakeRst) -> FakeRst:
        patcher = mock.patch.object(rst_mcp, "call", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class SerialTests(SimpleTestCase):
    def test_variants_cover_both_alphabets(self):
        assert serial_variants(" C2674623 ") == ["C2674623", "С2674623"]
        assert serial_variants("С2674623") == ["С2674623", "C2674623"]
        assert serial_variants("12345678") == ["12345678"]

    def test_pattern_hides_lookalike_letters(self):
        assert serial_pattern("С2674623") == serial_pattern("c2674623") == "?2674623"
        assert serial_pattern("Ю753") == "Ю753" and serial_pattern("Ч 1253") == "Ч 1253"

    def test_key_ignores_spaces_case_and_alphabet(self):
        assert serial_key("с3403520 11") == serial_key("C340352011")


@override_settings(**RST)
class HistoryTests(Base, SimpleTestCase):
    def test_history_is_collected_year_by_year(self):
        """За несколько лет сразу ФГИС не отвечает — на каждый год свой запрос."""
        fake = self.use(FakeRst())
        with mock.patch("apps.arshin.fif.requests.get") as get:
            history = VriClient().history(SERIAL, today=TODAY)
        get.assert_not_called()
        assert sorted(fake.searches()) == [(PATTERN, y) for y in (2023, 2024, 2025, 2026)]
        assert history.years == (2023, 2026) and not history.partial
        item = history.items[0]
        assert item.vri_id == "1-529062899" and item.mit_number == "24319-05"
        assert item.modification == "СВ-15Г" and item.applicable is True
        assert item.verification_date == "2026-05-27" and item.valid_date == "2032-05-26"

    def test_both_alphabets_are_shown_and_strangers_dropped(self):
        """«С» русская и латинская — разные приборы в реестре: показываем оба, «Z2674623» — нет."""
        self.use(FakeRst())
        history = VriClient().history(SERIAL_LATIN, today=TODAY)
        assert [(i.mi_number, i.mit_number) for i in history.items] == [
            (SERIAL, "24319-05"), (SERIAL_LATIN, "25913-08"),
        ]

    def test_second_call_comes_from_cache(self):
        fake = self.use(FakeRst())
        VriClient().history(SERIAL, today=TODAY)
        asked = len(fake.calls)
        again = VriClient().history(SERIAL, today=TODAY)
        assert len(fake.calls) == asked and len(again.items) == 2

    def test_failed_past_year_gives_partial_answer_and_is_not_cached(self):
        fake = self.use(FakeRst(fail_years=(2024,)))
        with mock.patch("apps.arshin.fif.requests.get", side_effect=__import__("requests").ConnectionError()):
            history = VriClient().history(SERIAL, today=TODAY)
        assert history.partial and history.failed_years == [2024] and len(history.items) == 2
        asked = len(fake.calls)
        VriClient().history(SERIAL, today=TODAY)
        assert len(fake.calls) > asked

    def test_dead_registry_fails_after_one_year(self):
        fake = self.use(FakeRst(dead=True))
        with mock.patch("apps.arshin.fif.requests.get", side_effect=__import__("requests").ConnectionError()):
            with self.assertRaises(FifUnavailable):
                VriClient().history(SERIAL, today=TODAY)
        assert fake.searches() == [(PATTERN, 2026)]

    def test_short_serial_asks_nothing(self):
        fake = self.use(FakeRst())
        assert VriClient().history("12", today=TODAY).items == []
        assert fake.calls == []

    def test_direct_route_when_mcp_is_down(self):
        self.use(FakeRst(dead=True))

        def get(url, params=None, timeout=None):
            response = mock.Mock()
            response.raise_for_status.return_value = None
            hit = params["mi_number"] == PATTERN and params["year"] == 2026
            response.json.return_value = SAMPLES["direct_year" if hit else "direct_empty"]
            assert url.endswith("/vri") and "verification_date_start" not in params
            return response

        with mock.patch("apps.arshin.fif.requests.get", side_effect=get):
            history = VriClient().history(SERIAL, today=TODAY)
        (item,) = history.items
        assert item.verification_date == "2026-05-27" and item.valid_date == "2032-05-26"  # из ДД.ММ.ГГГГ


@override_settings(**RST)
class CardTests(Base, SimpleTestCase):
    def test_card_from_mcp_raw(self):
        fake = self.use(FakeRst())
        card = VriClient().card("https://fgis.gost.ru/fundmetrology/cm/results/1-529062899")
        assert fake.calls == [("get_verification", {"vri_id": "1-529062899", "include_raw": True})]
        assert card.mit_number == "24319-05" and card.modification == "СВ-15Г"
        assert card.mit_uuid == "97cf5791-eb65-5e2c-a496-6b237f7f1d97"
        assert card.owner == "г.Киров, ул.Кольцова 22к1-71" and card.method == "МИ 1592-2015"
        assert card.applicable is True and card.docnum == "С-ДЮЕ/27-05-2026/529062899"
        assert card.verification_date == "2026-05-27" and card.manufacture_year is None
        assert card.standards[0]["notation"] == "УПСЖ 5П"

    def test_direct_format_year_and_unsuitable(self):
        raw = copy.deepcopy(SAMPLES["card"]["raw"])
        raw["miInfo"]["singleMI"]["manufactureYear"] = 2015
        raw["miInfo"]["singleMI"]["modification"] = "нет модификации"
        raw["vriInfo"].pop("applicable")
        raw["vriInfo"]["inapplicable"] = {"noticeNum": "И-ДЮЕ/27-05-2026/1"}
        card = VriCard.parse({"result": raw}, "1-1")
        assert card.manufacture_year == 2015 and card.modification == ""
        assert card.applicable is False and card.docnum == "И-ДЮЕ/27-05-2026/1"

    def test_bad_id_is_refused_without_request(self):
        fake = self.use(FakeRst())
        with self.assertRaises(FifUnavailable):
            VriClient().card("не-номер")
        assert fake.calls == []


@override_settings(**RST)
class ApiTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(User.objects.create_user("v@ei.test", "pw"))
        self.family = f.make_family()
        patcher = mock.patch("apps.arshin.vri.dt.date", wraps=dt.date)
        patcher.start().today.return_value = TODAY
        self.addCleanup(patcher.stop)

    def get(self, serial):
        return self.api.get(reverse("instrument_history"), {"serial": serial})

    def test_fgis_row_gets_our_type_by_number_and_modification(self):
        self.use(FakeRst())
        hot = f.make_si_type(self.family, registry_number="24319-05", name="СВ-15Г")
        f.make_si_type(self.family, registry_number="24319-05", name="СВ-15Х")
        body = self.get(SERIAL_LATIN).json()
        assert body["fgis"]["ok"] and len(body["fgis"]["items"]) == 2
        item = body["fgis"]["items"][0]
        assert item["si_type"]["id"] == hot.pk
        assert [c["name"] for c in item["candidates"]] == ["СВ-15Г", "СВ-15Х"]

    def test_type_is_not_guessed_when_modification_differs(self):
        self.use(FakeRst())
        f.make_si_type(self.family, registry_number="24319-05", name="СВ-15Х")
        item = self.get(SERIAL).json()["fgis"]["items"][0]
        assert item["si_type"] is None and len(item["candidates"]) == 1

    def test_own_instrument_is_found_even_when_fgis_is_down(self):
        self.use(FakeRst(dead=True))
        employee = f.make_employee()
        verification = f.make_verification(self.family, employee, day=5, serial=SERIAL)
        instrument = verification.instrument
        instrument.manufacture_year = 2015
        instrument.save()
        with mock.patch("apps.arshin.fif.requests.get", side_effect=__import__("requests").ConnectionError()):
            response = self.get(SERIAL_LATIN)
        assert response.status_code == 200
        body = response.json()
        assert body["fgis"]["ok"] is False and body["fgis"]["error"]
        (own,) = body["own"]
        assert own["manufacture_year"] == 2015 and own["si_type"]["id"] == instrument.si_type_id
        assert own["verifications_count"] == 1 and own["last"]["verified_at"].startswith("2026-03-05")

    def test_sources_can_be_asked_separately(self):
        fake = self.use(FakeRst())
        own = self.api.get(reverse("instrument_history"), {"serial": SERIAL, "source": "own"}).json()
        assert "fgis" not in own and own["own"] == [] and fake.calls == []
        fgis = self.api.get(reverse("instrument_history"), {"serial": SERIAL, "source": "fgis"}).json()
        assert "own" not in fgis and len(fgis["fgis"]["items"]) == 2

    def test_short_serial_returns_empty(self):
        fake = self.use(FakeRst())
        body = self.get("1").json()
        assert body["own"] == [] and body["fgis"]["items"] == [] and fake.calls == []

    def test_card_endpoint(self):
        self.use(FakeRst())
        hot = f.make_si_type(self.family, registry_number="24319-05", name="СВ-15Г")
        body = self.api.get(reverse("fgis_verification_card", args=["1-529062899"])).json()
        assert body["method"] == "МИ 1592-2015" and body["si_type"]["id"] == hot.pk

    def test_needs_login(self):
        assert APIClient().get(reverse("instrument_history"), {"serial": SERIAL}).status_code == 401
