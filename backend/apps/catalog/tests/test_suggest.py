"""Подсказка типов СИ при заведении прибора."""

from __future__ import annotations

from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.arshin.fif import FifType, FifUnavailable
from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import User

URL_NAME = "si_type_suggest"


class SuggestTestCase(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("m@ei.test", "pw"))
        self.family = MeasurementFamily.objects.create(
            code="water", name="Счётчики воды"
        )
        SiType.objects.create(
            family=self.family, registry_number="12345-06", name="СВК-15",
            manufacturer="Тепловодомер",
        )

    def get(self, query: str):
        return self.client.get(reverse(URL_NAME), {"q": query})

    def test_short_query_returns_nothing(self):
        assert self.get("С").data == []

    @mock.patch("apps.catalog.api.FifClient")
    def test_local_types_come_first(self, fif):
        fif.return_value.search_types.return_value = []
        results = self.get("СВК").data["results"]

        assert results[0]["source"] == "local"
        assert results[0]["manufacturer"] == "Тепловодомер"
        assert results[0]["si_type_id"] is not None

    @mock.patch("apps.catalog.api.FifClient")
    def test_same_name_from_another_manufacturer_is_flagged(self, fif):
        """Локальный СВК-15 и такой же из ФИФ от другого изготовителя."""
        fif.return_value.search_types.return_value = [
            FifType(registry_number="98765-22", notation="СВК-15", manufacturer="Бетар")
        ]
        results = self.get("СВК").data["results"]

        assert len(results) == 2
        assert all(item["ambiguous"] for item in results)
        assert {item["manufacturer"] for item in results} == {"Тепловодомер", "Бетар"}

    @mock.patch("apps.catalog.api.FifClient")
    def test_duplicate_from_fif_is_not_shown_twice(self, fif):
        fif.return_value.search_types.return_value = [
            FifType(registry_number="12345-06", notation="СВК-15", manufacturer="Тепловодомер")
        ]
        results = self.get("СВК").data["results"]

        assert len(results) == 1
        assert results[0]["source"] == "local"

    @mock.patch("apps.catalog.api.FifClient")
    def test_registry_outage_does_not_block_the_work(self, fif):
        """Реестр лёг — локальные варианты всё равно отдаём, с предупреждением."""
        fif.return_value.search_types.side_effect = FifUnavailable("Реестр ФИФ недоступен: timeout")
        payload = self.get("СВК").data

        assert len(payload["results"]) == 1
        assert "недоступен" in payload["warning"]


BETAR = [
    FifType(registry_number="20067-00", notation="СХВ-15, СГВ-15", manufacturer="Видеофон"),
    FifType(registry_number="16078-97", notation="СГВ-15", manufacturer="Бетар"),
    FifType(registry_number="16078-13", notation="СГВ-15", manufacturer="Бетар"),
    FifType(registry_number="16078-05", notation="СГВ-15", manufacturer="Бетар"),
    FifType(registry_number="16078-22", notation="СГВ-15", manufacturer="Бетар"),
]


@mock.patch("apps.catalog.api.FifClient")
class SuggestByYearTestCase(TestCase):
    """Год выпуска отсекает записи реестра, по которым прибор выпущен быть не мог."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("m@ei.test", "pw"))

    def numbers(self, fif, **params) -> list[str]:
        fif.return_value.search_types.return_value = BETAR
        self.payload = self.client.get(reverse(URL_NAME), {"q": "СГВ-15", **params}).data
        return [item["registry_number"] for item in self.payload["results"]]

    def test_without_year_everything_is_shown(self, fif):
        assert len(self.numbers(fif)) == 5
        assert self.payload["hidden_by_year"] == 0

    def test_year_keeps_the_record_in_force(self, fif):
        """Бетар 2020 года: не 16078-05 (сменён в 2013-м) и не 16078-22 (ещё не было)."""
        assert self.numbers(fif, year="2020") == ["20067-00", "16078-13"]
        assert self.payload["hidden_by_year"] == 3

    def test_old_meter_gets_the_old_record(self, fif):
        assert self.numbers(fif, year="2010") == ["20067-00", "16078-05"]
        assert self.numbers(fif, year="1999") == ["16078-97"]

    def test_year_of_renewal_keeps_both_records(self, fif):
        """В 2013-м счётчик могли выпустить и по 16078-05, и по 16078-13."""
        assert self.numbers(fif, year="2013") == ["20067-00", "16078-13", "16078-05"]

    def test_garbage_year_is_ignored(self, fif):
        assert len(self.numbers(fif, year="20")) == 5
        assert len(self.numbers(fif, year="abcd")) == 5

    def test_survivor_is_not_called_ambiguous(self, fif):
        """Двойники по названию ушли по году — предупреждать «сверьте изготовителя» не о чем."""
        self.numbers(fif, year="2020")
        assert [item["ambiguous"] for item in self.payload["results"]] == [False, False]

    def test_local_types_are_filtered_too(self, fif):
        family = MeasurementFamily.objects.create(code="water", name="Счётчики воды")
        SiType.objects.create(family=family, registry_number="16078-05", name="СГВ-15", manufacturer="Бетар")
        assert "16078-05" not in self.numbers(fif, year="2020")
        assert "16078-05" in self.numbers(fif)
