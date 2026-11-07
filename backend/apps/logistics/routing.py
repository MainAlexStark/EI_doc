"""Маршрут по точкам: матрица времени в пути и порядок объезда.

Матрица — из OSRM (`/table`), если задан LOGISTICS_OSRM_URL, иначе по прямой
(гаверсинус) с коэффициентом извилистости дорог и средней скоростью. OSRM
недоступен — тихо считаем по прямой: маршрут хуже, но планировщик работает.

Порядок — OR-Tools (если пакет установлен: `pip install ortools`) с учётом
временных окон, иначе ближайший сосед + 2-opt. Для городского дня в 10–20
объектов 2-opt даёт решение в пределах нескольких процентов от оптимума.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field

import requests
from django.conf import settings

log = logging.getLogger(__name__)

EARTH_KM = 6371.0


@dataclass
class Point:
    key: object
    lat: float
    lon: float
    window_from: dt.time | None = None
    window_to: dt.time | None = None
    service_minutes: int = 30


@dataclass
class Route:
    order: list[int]                      # индексы точек (без депо)
    minutes: list[float]                  # прибытие от начала дня, по order
    total_minutes: float
    total_km: float | None
    source: str                           # "osrm" | "straight"
    solver: str                           # "ortools" | "2opt"
    warnings: list[str] = field(default_factory=list)


def _setting(name: str, default):
    return getattr(settings, name, default)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def straight_matrix(coords: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
    """Минуты и километры по прямой с поправкой на дороги."""
    detour = float(_setting("LOGISTICS_DETOUR_FACTOR", 1.35))
    speed = float(_setting("LOGISTICS_SPEED_KMH", 32))
    km = [[haversine_km(a, b) * detour for b in coords] for a in coords]
    minutes = [[d / speed * 60 for d in row] for row in km]
    return minutes, km


def osrm_matrix(coords: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]] | None:
    base = (_setting("LOGISTICS_OSRM_URL", "") or "").rstrip("/")
    if not base or len(coords) < 2:
        return None
    path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    try:
        response = requests.get(
            f"{base}/table/v1/driving/{path}", params={"annotations": "duration,distance"}, timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "Ok":
            raise ValueError(data.get("message") or data.get("code"))
        minutes = [[(v or 0) / 60 for v in row] for row in data["durations"]]
        km = [[(v or 0) / 1000 for v in row] for row in data.get("distances") or []] or None
        return minutes, km
    except Exception as exc:  # сеть, формат, нет данных региона
        log.warning("OSRM недоступен, считаю по прямой: %s", exc)
        return None


def matrix(coords: list[tuple[float, float]]):
    got = osrm_matrix(coords)
    if got is not None:
        return got[0], got[1], "osrm"
    minutes, km = straight_matrix(coords)
    return minutes, km, "straight"


def _tour_cost(tour: list[int], cost: list[list[float]]) -> float:
    return sum(cost[a][b] for a, b in zip(tour, tour[1:]))


def nearest_neighbour(cost: list[list[float]], start: int = 0) -> list[int]:
    n = len(cost)
    tour, left = [start], set(range(n)) - {start}
    while left:
        here = tour[-1]
        nxt = min(left, key=lambda j: cost[here][j])
        tour.append(nxt)
        left.remove(nxt)
    return tour


def two_opt(tour: list[int], cost: list[list[float]]) -> list[int]:
    """Улучшение открытого маршрута с закреплённым началом (депо)."""
    best = tour[:]
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best) - 1):
            for k in range(i + 1, len(best)):
                candidate = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                if _tour_cost(candidate, cost) + 1e-9 < _tour_cost(best, cost):
                    best = candidate
                    improved = True
    return best


def _minutes_of(value: dt.time | None, day_start: dt.time) -> float | None:
    if value is None:
        return None
    return (value.hour * 60 + value.minute) - (day_start.hour * 60 + day_start.minute)


def _ortools_order(cost, points: list[Point], day_start: dt.time, horizon: int) -> list[int] | None:
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError:
        return None
    n = len(cost)
    manager = pywrapcp.RoutingIndexManager(n, 1, [0], [0])
    routing = pywrapcp.RoutingModel(manager)
    service = [0] + [p.service_minutes for p in points]

    def transit(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return int(round(cost[a][b] + service[a])) if b != 0 else 0

    callback = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(callback)
    routing.AddDimension(callback, 120, horizon, True, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    for node, point in enumerate(points, start=1):
        lo = _minutes_of(point.window_from, day_start)
        hi = _minutes_of(point.window_to, day_start)
        if lo is not None or hi is not None:
            time_dim.CumulVar(manager.NodeToIndex(node)).SetRange(max(0, int(lo or 0)), int(hi if hi is not None else horizon))
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = 3
    solution = routing.SolveWithParameters(params)
    if solution is None:
        return None
    order, index = [], routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node:
            order.append(node)
        index = solution.Value(routing.NextVar(index))
    return order


def plan_route(depot: tuple[float, float], points: list[Point], *, day_start: dt.time = dt.time(9, 0),
               day_minutes: int = 9 * 60) -> Route:
    """Порядок объезда точек из депо и расчётное время прибытия."""
    if not points:
        return Route(order=[], minutes=[], total_minutes=0, total_km=0, source="none", solver="none")
    coords = [depot] + [(p.lat, p.lon) for p in points]
    minutes, km, source = matrix(coords)

    solver = "ortools"
    tour_nodes = _ortools_order(minutes, points, day_start, day_minutes * 2)
    if tour_nodes is None:
        solver = "2opt"
        # Без OR-Tools окна не учитываются при поиске порядка, только при
        # расчёте прибытия: опоздания попадают в warnings.
        tour = two_opt(nearest_neighbour(minutes, 0), minutes)
        tour_nodes = tour[1:]

    arrivals, warnings = [], []
    clock, prev = 0.0, 0
    for node in tour_nodes:
        point = points[node - 1]
        clock += minutes[prev][node]
        lo = _minutes_of(point.window_from, day_start)
        hi = _minutes_of(point.window_to, day_start)
        if lo is not None and clock < lo:
            clock = lo  # приехали раньше окна — ждём
        if hi is not None and clock > hi:
            warnings.append(f"{point.key}: прибытие позже окна ({_fmt(day_start, clock)} > {point.window_to:%H:%M})")
        arrivals.append(clock)
        clock += point.service_minutes
        prev = node
    if clock > day_minutes:
        warnings.append(f"день не укладывается в {day_minutes // 60} ч: нужно {clock / 60:.1f} ч")

    total_km = None
    if km:
        total_km = sum(km[a][b] for a, b in zip([0] + tour_nodes, tour_nodes))
    return Route(order=[n - 1 for n in tour_nodes], minutes=arrivals, total_minutes=clock,
                 total_km=total_km, source=source, solver=solver, warnings=warnings)


def _fmt(day_start: dt.time, minutes: float) -> str:
    base = dt.datetime.combine(dt.date.today(), day_start)
    return (base + dt.timedelta(minutes=minutes)).strftime("%H:%M")


def eta(day_start: dt.time, minutes: float) -> dt.time:
    base = dt.datetime.combine(dt.date(2000, 1, 1), day_start)
    return (base + dt.timedelta(minutes=round(minutes))).time()
