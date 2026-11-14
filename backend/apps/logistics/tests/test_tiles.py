"""Тайлы карты через наш сервер: скачать один раз, отдавать с диска, заглушку не кэшировать."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.logistics import tiles

PNG = b"\x89PNG\r\n\x1a\nfake"


def answer(status=200, body=PNG, content_type="image/png"):
    return mock.Mock(status_code=status, content=body, headers={"Content-Type": content_type})


class TileProxyTests(TestCase):
    def setUp(self) -> None:
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media, MAP_TILES_CONTACT="info@example.ru")
        override.enable()
        self.addCleanup(override.disable)
        cache.clear()

    def test_downloads_once_then_serves_from_disk(self):
        with mock.patch("apps.logistics.tiles.requests.get", return_value=answer()) as get:
            first = self.client.get("/tiles/12/2614/1225.png")
            second = self.client.get("/tiles/12/2614/1225.png")
        assert first.status_code == second.status_code == 200
        assert first.content == second.content == PNG and first["Content-Type"] == "image/png"
        assert "max-age" in first["Cache-Control"]
        get.assert_called_once()
        assert get.call_args.args[0] == "https://tile.openstreetmap.org/12/2614/1225.png"
        assert "EI_doc" in get.call_args.kwargs["headers"]["User-Agent"]
        assert "info@example.ru" in get.call_args.kwargs["headers"]["User-Agent"]
        assert tiles.cache_path(12, 2614, 1225).exists()

    def test_blocked_answer_is_not_cached_or_served(self):
        blocked = answer(403, b"<blocked>", "image/png")
        with mock.patch("apps.logistics.tiles.requests.get", return_value=blocked):
            response = self.client.get("/tiles/12/2614/1225.png")
        assert response.status_code == 502 and response["Cache-Control"] == "no-store"
        assert not tiles.cache_path(12, 2614, 1225).exists()
        html = answer(200, b"<html>", "text/html")
        with mock.patch("apps.logistics.tiles.requests.get", return_value=html):
            assert self.client.get("/tiles/12/2614/1225.png").status_code == 502

    def test_stale_tile_is_better_than_nothing(self):
        path = tiles.cache_path(10, 653, 306)
        path.parent.mkdir(parents=True)
        path.write_bytes(PNG)
        old = time.time() - tiles.FRESH_SECONDS - 10
        os.utime(path, (old, old))
        with mock.patch("apps.logistics.tiles.requests.get", side_effect=requests.ConnectionError("нет сети")):
            response = self.client.get("/tiles/10/653/306.png")
        assert response.status_code == 200 and response.content == PNG

    def test_impossible_tiles_are_refused_without_going_out(self):
        with mock.patch("apps.logistics.tiles.requests.get") as get:
            assert self.client.get("/tiles/20/1/1.png").status_code == 404
            assert self.client.get("/tiles/3/8/0.png").status_code == 404
            get.assert_not_called()

    @override_settings(MAP_TILES_UPSTREAM="https://tiles.example.org/{z}/{x}/{y}.png")
    def test_upstream_is_configurable(self):
        with mock.patch("apps.logistics.tiles.requests.get", return_value=answer()) as get:
            self.client.get("/tiles/5/19/9.png")
        assert get.call_args.args[0] == "https://tiles.example.org/5/19/9.png"
