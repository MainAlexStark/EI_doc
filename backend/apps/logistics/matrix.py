"""Сколько ехать между адресами: матрица времени в пути на заданный момент выезда.

Источники:

* **2ГИС** (``DGIS_API_KEY``) — Distance Matrix API с учётом пробок: на ближайший
  час — текущие пробки, на потом — прогноз по статистике на это время. Синхронный
  запрос берёт до 25 точек отправления и 25 назначения, поэтому большая матрица
  собирается блоками 25×25.
* **оценка** — если ключа нет или 2ГИС не ответил: расстояние по прямой,
  поправка на извилистость дорог и средняя скорость (в городе меньше, за городом
  больше). Порядок объезда по ней получается разумный, время — приблизительное,
  и экран честно пишет, что пробки не учтены.

Матрица кэшируется на 10 минут: пересчёт маршрута с другими настройками не
должен каждый раз ходить в платный API.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

DGIS_URL = "https://routing.api.2gis.com/get_dist_matrix"
BLOCK = 25
CACHE_SECONDS = 600

Point = tuple[float, float]


@dataclass
class Matrix:
    seconds: list[list[int]]
    meters: list[list[int]]
    provider: str  # "2gis" | "estimate"
    traffic: bool
    label: str
    notes: list[str] = field(default_factory=list)


def haversine_km(a: Point, b: Point) -> float:
    rad = math.pi / 180
    d_lat = (b[0] - a[0]) * rad
    d_lon = (b[1] - a[1]) * rad
    h = math.sin(d_lat / 2) ** 2 + math.cos(a[0] * rad) * math.cos(b[0] * rad) * math.sin(d_lon / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def estimate(points: list[Point]) -> Matrix:
    """Без сервиса маршрутов: по прямой × 1,35, в черте города ~24 км/ч, дальше ~55 км/ч."""
    n = len(points)
    seconds = [[0] * n for _ in range(n)]
    meters = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            road_km = haversine_km(points[i], points[j]) * 1.35
            speed = 24 if road_km < 12 else 55
            seconds[i][j] = int(road_km / speed * 3600) + (120 if road_km > 0.05 else 0)  # парковка, подъезд
            meters[i][j] = int(road_km * 1000)
    return Matrix(seconds, meters, "estimate", False, "оценка по прямой, без пробок")


def _dgis_block(key: str, points: list[Point], sources: list[int], targets: list[int],
                departure: dt.datetime, with_time: bool) -> list[dict]:
    body: dict = {
        "points": [{"lat": lat, "lon": lon} for lat, lon in points],
        "sources": sources,
        "targets": targets,
    }
    if with_time:
        # Ближайший час — текущие пробки, дальше — прогноз по статистике на время выезда.
        soon = departure - timezone.now() < dt.timedelta(hours=1)
        body["type"] = "jam" if soon else "statistics"
        body["start_time"] = departure.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = requests.post(
        DGIS_URL, params={"key": key, "version": "2.0"}, json=body, timeout=20,
    )
    if response.status_code >= 400:
        raise requests.HTTPError(f"{response.status_code}: {response.text[:200]}", response=response)
    return response.json().get("routes") or []


def dgis(points: list[Point], departure: dt.datetime) -> Matrix:
    key = getattr(settings, "DGIS_API_KEY", "")
    n = len(points)
    seconds = [[0] * n for _ in range(n)]
    meters = [[0] * n for _ in range(n)]
    fallback = estimate(points)
    missing = 0
    with_time = True
    blocks = [list(range(start, min(start + BLOCK, n))) for start in range(0, n, BLOCK)]
    for sources in blocks:
        for targets in blocks:
            sub = sorted(set(sources) | set(targets))
            index = {global_id: local for local, global_id in enumerate(sub)}
            args = ([points[i] for i in sub], [index[i] for i in sources], [index[i] for i in targets])
            try:
                routes = _dgis_block(key, *args, departure, with_time)
            except requests.HTTPError as exc:
                if not with_time or exc.response is None or exc.response.status_code not in (400, 422):
                    raise
                # Тариф или версия API не принимают время выезда — считаем по текущим пробкам.
                logger.info("2ГИС не принял время выезда (%s), считаю без него", exc)
                with_time = False
                routes = _dgis_block(key, *args, departure, with_time)
            seen = set()
            for route in routes:
                if route.get("status") != "OK":
                    continue
                i, j = sub[route["source_id"]], sub[route["target_id"]]
                seconds[i][j] = int(route.get("duration") or 0)
                meters[i][j] = int(route.get("distance") or 0)
                seen.add((i, j))
            for i in sources:
                for j in targets:
                    if i != j and (i, j) not in seen:
                        seconds[i][j] = fallback.seconds[i][j]
                        meters[i][j] = fallback.meters[i][j]
                        missing += 1
    notes = []
    if missing:
        notes.append(f"для {missing} пар адресов 2ГИС не нашёл дорогу — время оценено по прямой")
    label = "2ГИС с учётом пробок" if with_time else "2ГИС, текущие пробки"
    return Matrix(seconds, meters, "2gis", True, label, notes)


def build(points: list[Point], departure: dt.datetime) -> Matrix:
    """Матрица на момент выезда. Никогда не падает: при сбое сервиса — оценка с пометкой."""
    if len(points) < 2:
        return Matrix([[0] * len(points) for _ in points], [[0] * len(points) for _ in points],
                      "estimate", False, "один адрес")
    key = getattr(settings, "DGIS_API_KEY", "")
    if not key:
        return estimate(points)
    stamp = departure.replace(minute=departure.minute - departure.minute % 15, second=0, microsecond=0)
    digest = hashlib.sha1(json.dumps([points, stamp.isoformat()]).encode()).hexdigest()
    cached = cache.get(f"logistics:matrix:{digest}")
    if cached:
        return Matrix(**cached)
    try:
        result = dgis(points, departure)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        logger.warning("2ГИС недоступен: %s", exc)
        result = estimate(points)
        result.notes.append("2ГИС не ответил — время оценено по прямой, без пробок")
        return result
    cache.set(f"logistics:matrix:{digest}", result.__dict__, CACHE_SECONDS)
    return result
