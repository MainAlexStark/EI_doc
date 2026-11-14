"""Тайлы карты через наш сервер: ``GET /tiles/<z>/<x>/<y>.png``.

Браузер поверителя к tile.openstreetmap.org больше не ходит. OSM блокирует
запросы, которые не может опознать или которых слишком много («Access blocked»,
osm.wiki/Blocked), и снаружи не узнать, за что именно: за Referer, за общий IP
мобильного оператора или VPN. Сервер ходит сам — с понятным User-Agent, и
каждый тайл скачивает один раз. Дальше он лежит на диске (``MEDIA_ROOT/tiles``)
и отдаётся всем сотрудникам. Политика OSM такое разрешает и даже просит:
кэшировать, представляться, не качать районы целиком.

Источник — ``MAP_TILES_UPSTREAM``. Если OSM заблокирует и сервер, туда ставится
другой растровый источник с тем же шаблоном ``{z}/{x}/{y}``.

Без авторизации: ``<img>`` в Leaflet токен не передаёт, а сами тайлы — публичная
карта. От перебора защищают пределы зума и координат и троттлинг по IP.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests
from django.conf import settings
from django.http import Http404, HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

MAX_ZOOM = 19
FRESH_SECONDS = 30 * 24 * 3600  # OSM обновляет тайлы, но для объезда адресов месяц — нормально
BROWSER_MAX_AGE = 7 * 24 * 3600


def upstream() -> str:
    return getattr(settings, "MAP_TILES_UPSTREAM", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")


def user_agent() -> str:
    contact = getattr(settings, "MAP_TILES_CONTACT", "") or "eicounters.ru"
    return f"EI_doc/1.0 (field trip planner; {contact})"


def cache_path(z: int, x: int, y: int) -> Path:
    return Path(settings.MEDIA_ROOT) / "tiles" / str(z) / str(x) / f"{y}.png"


class TileThrottle(AnonRateThrottle):
    """Обычная карта на телефоне — сотни тайлов за сеанс. Перебор района — тысячи."""

    scope = "map_tiles"


def _download(z: int, x: int, y: int) -> bytes | None:
    url = upstream().format(z=z, x=x, y=y, s="a")
    headers = {"User-Agent": user_agent()}
    site = getattr(settings, "MAP_TILES_REFERER", "")
    if site:
        headers["Referer"] = site
    try:
        response = requests.get(url, headers=headers, timeout=6)
    except requests.RequestException as exc:
        logger.warning("tiles: источник недоступен: %s", exc)
        return None
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not content_type.startswith("image/"):
        # Заглушку «Access blocked» (403) не кэшируем и не отдаём как карту.
        logger.warning("tiles: %s ответил %s (%s)", url, response.status_code, content_type)
        return None
    return response.content


def _store(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_bytes(body)
    temp.replace(path)  # атомарно: соседний процесс не прочитает половину файла


class TileView(APIView):
    """GET /tiles/<z>/<x>/<y>.png"""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [TileThrottle]

    def get(self, request, z: int, x: int, y: int):
        if z > MAX_ZOOM or x >= 2**z or y >= 2**z:
            raise Http404("Нет такого тайла")
        path = cache_path(z, x, y)
        body = None
        try:
            stat = path.stat()
            if time.time() - stat.st_mtime < FRESH_SECONDS:
                body = path.read_bytes()
        except OSError:
            pass
        if body is None:
            fresh = _download(z, x, y)
            if fresh is not None:
                _store(path, fresh)
                body = fresh
            elif path.exists():
                body = path.read_bytes()  # устаревший тайл лучше, чем дыра в карте
        if body is None:
            response = HttpResponse(status=502)
            response["Cache-Control"] = "no-store"
            return response
        response = HttpResponse(body, content_type="image/png")
        response["Cache-Control"] = f"public, max-age={BROWSER_MAX_AGE}"
        return response
