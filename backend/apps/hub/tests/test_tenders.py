"""Госзакупки: разбор RSS ЕИС, сохранение без дублей, ошибки сети, API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from unittest import mock

import requests
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Role, User
from apps.hub import tenders
from apps.hub.models import Tender, TenderSearch

RSS = (Path(__file__).parent / "fixtures" / "eis_rss.xml").read_bytes()


def fake_session(content=RSS, error=None):
    session = mock.Mock()
    if error:
        session.get.side_effect = error
    else:
        response = mock.Mock(content=content)
        response.raise_for_status.return_value = None
        session.get.return_value = response
    return session


class ParseTestCase(TestCase):
    def test_parses_both_laws(self):
        rows = tenders.parse_rss(RSS)
        assert len(rows) == 2
        first, second = rows
        assert first["reg_number"] == "0340200003326012345"
        assert first["title"] == "Оказание услуг по поверке приборов учёта воды"
        assert first["price"] == Decimal("245300.00")
        assert first["deadline"] == dt.date(2026, 11, 20)
        assert first["customer"].startswith("МКУ")
        assert first["law"] == "44"
        assert second["law"] == "223" and second["price"] is None
        assert second["customer"] == "АО «Вятка-Энерго»"

    def test_garbage_is_reported(self):
        with self.assertRaises(tenders.TendersUnavailable):
            tenders.parse_rss(b"<html>captcha</html")


class RunTestCase(TestCase):
    def test_new_tenders_are_saved_once_and_notified(self):
        TenderSearch.objects.create(query="поверка", region="43")
        session = fake_session()
        with mock.patch("apps.hub.tenders.notify") as notify:
            assert tenders.run_all(session=session) == 2
            assert tenders.run_all(session=session) == 0
        assert notify.call_count == 1
        assert Tender.objects.count() == 2
        params = session.get.call_args.kwargs["params"]
        assert params["fz44"] == "on" and params["fz223"] == "on" and params["customerPlace"] == "43"

    def test_network_error_is_remembered(self):
        search = TenderSearch.objects.create(query="поверка")
        assert tenders.run_all(session=fake_session(error=requests.ConnectionError("нет"))) == 0
        search.refresh_from_db()
        assert "недоступна" in search.last_error

    def test_api(self):
        api = APIClient()
        api.force_authenticate(User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER))
        assert api.get(reverse("tender_list")).status_code == 403
        api.force_authenticate(User.objects.create_user("m@ei.test", "pw", role=Role.MANAGER))
        with mock.patch("apps.hub.tenders.requests", mock.Mock(get=fake_session().get,
                                                                RequestException=requests.RequestException)):
            created = api.post(reverse("tender_list"), {"query": "поверка счетчиков"}, format="json")
        assert created.status_code == 201 and created.json()["found"] == 2
        listing = api.get(reverse("tender_list")).json()
        tender_id = listing["tenders"][0]["id"]
        changed = api.patch(reverse("tender_detail", args=[tender_id]), {"status": "watching"}, format="json")
        assert changed.json()["status"] == "watching"
