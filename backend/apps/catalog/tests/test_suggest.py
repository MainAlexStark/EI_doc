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
            FifType(registry_number="98765-22", name="СВК-15", manufacturer="Бетар")
        ]
        results = self.get("СВК").data["results"]

        assert len(results) == 2
        assert all(item["ambiguous"] for item in results)
        assert {item["manufacturer"] for item in results} == {"Тепловодомер", "Бетар"}

    @mock.patch("apps.catalog.api.FifClient")
    def test_duplicate_from_fif_is_not_shown_twice(self, fif):
        fif.return_value.search_types.return_value = [
            FifType(registry_number="12345-06", name="СВК-15", manufacturer="Тепловодомер")
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
