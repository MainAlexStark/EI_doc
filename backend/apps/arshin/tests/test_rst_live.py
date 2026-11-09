"""Живая проверка Госреестра — по-настоящему ходит в сеть, поэтому по умолчанию пропускается.

    RST_LIVE=1 RST_MCP_URL=… RST_MCP_TOKEN=… pytest apps/arshin/tests/test_rst_live.py

Запускать на сервере после выката (или локально, если MCP-сервер доступен): показывает, что
поиск и карточка реально работают через тот путь, который выбран в настройках.
"""

from __future__ import annotations

import os

import pytest
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.arshin import status as fif_status
from apps.arshin.fif import FifClient

LIVE = os.environ.get("RST_LIVE") == "1"
SETTINGS = {"RST_MCP_URL": os.environ.get("RST_MCP_URL", ""), "RST_MCP_TOKEN": os.environ.get("RST_MCP_TOKEN", ""),
            "FIF_TRANSPORT": os.environ.get("FIF_TRANSPORT", "auto")}


@pytest.mark.skipif(not LIVE, reason="живой тест: RST_LIVE=1")
@override_settings(**SETTINGS)
class LiveRegistryTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        fif_status._local.clear()

    def test_search_and_card(self):
        found = FifClient(timeout=15).search_types("манометр", limit=5)
        assert found and found[0].title.lower().startswith("манометр")
        card = FifClient(timeout=15).get_type("98511-26")
        assert card.title == "Счетчики воды крыльчатые одноструйные"
        assert card.mpi and card.documents

    def test_status_says_registry_is_reachable(self):
        snapshot = fif_status.snapshot(probe=True)
        assert snapshot["ok"], snapshot
