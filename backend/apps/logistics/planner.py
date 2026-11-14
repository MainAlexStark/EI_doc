"""Порядок объезда с учётом времени в пути, пробок и окон клиентов (OR-Tools).

Задача — маршрут одного экипажа на день: выехать в заданное время откуда
сказали (офис, где я сейчас, первый адрес), объехать ждущие адреса, на каждом
провести сколько-то минут, успеть к тем, кому обещано время. Это задача
коммивояжёра с временными окнами; её решает OR-Tools (поиск с улучшением,
пара секунд на день из 30–60 адресов).

Окна мягкие: приехать раньше можно (ждём начала окна), опоздание не запрещено,
но дорого стоит. Жёсткие окна на реальном дне часто делают задачу
неразрешимой, а поверителю нужен план, а не ошибка: лучше маршрут с пометкой
«к 14:00 не успеваете на 20 минут».

Время в пути — ``apps.logistics.matrix`` на момент выезда (2ГИС с пробками
или оценка). Матрица считается один раз на момент выезда; для дня по городу
пробки от часа к часу меняются, но порядок от этого почти не страдает, а
прогноз приезда на каждый адрес экран всё равно пересчитывает от «я здесь».
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.logistics import geocode, matrix
from apps.logistics.models import StopStatus, Trip, TripStop

LATE_PENALTY = 30  # секунда опоздания стоит как 30 секунд в пути
SOLVER_SECONDS = 3


class PlanError(ValueError):
    """Построить нельзя — текст показывается человеку."""


@dataclass
class PlanOptions:
    start: str = "base"  # base | me | first
    me: tuple[float, float] | None = None
    departure: dt.datetime | None = None
    service_minutes: int = 20
    per_instrument_minutes: int = 5
    day_end: dt.time = dt.time(18, 0)
    return_to_start: bool = False


def base_point() -> tuple[float, float] | None:
    """Откуда выезжаем по умолчанию — офис. Координаты из настроек или по адресу (один раз)."""
    lat, lon = getattr(settings, "ROUTE_BASE_LAT", None), getattr(settings, "ROUTE_BASE_LON", None)
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    address = getattr(settings, "ROUTE_BASE_ADDRESS", "")
    if not address:
        return None
    key = "logistics:base_point"
    cached = cache.get(key)
    if cached:
        return tuple(cached)
    point = geocode.geocode(address)
    if point:
        cache.set(key, list(point), 7 * 24 * 3600)
    return point


def service_seconds(stop: TripStop, options: PlanOptions) -> int:
    if stop.service_minutes:
        return stop.service_minutes * 60
    extra = max((stop.planned_count or 1) - 1, 0) * options.per_instrument_minutes
    return (options.service_minutes + extra) * 60


def _offset(departure: dt.datetime, time: dt.time | None) -> int | None:
    if time is None:
        return None
    moment = timezone.make_aware(dt.datetime.combine(departure.astimezone(timezone.get_current_timezone()).date(), time))
    return int((moment - departure).total_seconds())


def _solve(seconds: list[list[int]], service: list[int], windows: list[tuple[int | None, int | None]],
           end_node: int | None) -> list[int]:
    """Порядок узлов 1..n-1 (0 — старт). ``end_node`` — куда вернуться; None — маршрут открыт."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    n = len(seconds)
    # Открытый маршрут: фиктивный конец, до которого ехать 0.
    size = n if end_node is not None else n + 1
    end = end_node if end_node is not None else n

    def travel(i: int, j: int) -> int:
        if i >= n or j >= n:
            return 0
        return seconds[i][j]

    manager = pywrapcp.RoutingIndexManager(size, 1, [0], [end])
    routing = pywrapcp.RoutingModel(manager)

    def drive_cb(a: int, b: int) -> int:
        return travel(manager.IndexToNode(a), manager.IndexToNode(b))

    def time_cb(a: int, b: int) -> int:
        i, j = manager.IndexToNode(a), manager.IndexToNode(b)
        return (service[i] if i < n else 0) + travel(i, j)

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(drive_cb))
    horizon = 3 * 24 * 3600
    routing.AddDimension(routing.RegisterTransitCallback(time_cb), horizon, horizon, True, "time")
    clock = routing.GetDimensionOrDie("time")
    # Важна не только дорога, но и весь день: ждать у закрытой двери до обеда хуже,
    # чем съездить к другим адресам и вернуться к обещанному времени.
    clock.SetSpanCostCoefficientForAllVehicles(1)
    for node in range(1, n):
        start, finish = windows[node]
        index = manager.NodeToIndex(node)
        if start is not None and start > 0:
            clock.CumulVar(index).SetMin(min(start, horizon))
        if finish is not None:
            clock.SetCumulVarSoftUpperBound(index, max(finish, 0), LATE_PENALTY)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(SOLVER_SECONDS if n > 3 else 1)
    solution = routing.SolveWithParameters(params)
    if solution is None:
        raise PlanError("Не удалось построить маршрут — попробуйте убрать окна времени")
    order = []
    index = solution.Value(routing.NextVar(routing.Start(0)))
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if 0 < node < n:
            order.append(node)
        index = solution.Value(routing.NextVar(index))
    return order


def plan(trip: Trip, options: PlanOptions, *, apply: bool = False) -> dict:
    """Построить порядок объезда ждущих адресов; ``apply`` — сохранить порядок и время приезда."""
    stops = list(trip.stops.select_related("site").order_by("position", "id"))
    waiting = [stop for stop in stops if stop.status == StopStatus.PLANNED and stop.site.latitude is not None]
    blind = [stop for stop in stops if stop.status == StopStatus.PLANNED and stop.site.latitude is None]
    finished = [stop for stop in stops if stop.status != StopStatus.PLANNED]
    if not waiting:
        raise PlanError("Нет ждущих адресов с точкой на карте")

    now = timezone.now()
    departure = options.departure or now
    tz = timezone.get_current_timezone()

    first_fixed: TripStop | None = None
    if options.start == "me":
        if not options.me:
            raise PlanError("Не пришло ваше местоположение")
        origin = options.me
        origin_label = "от вас"
    elif options.start == "first":
        first_fixed = waiting.pop(0)
        origin = (float(first_fixed.site.latitude), float(first_fixed.site.longitude))
        origin_label = f"от адреса {first_fixed.site.address}"
    else:
        origin = base_point()
        if origin is None:
            raise PlanError("Адрес офиса не задан (ROUTE_BASE_ADDRESS) — выберите «от меня» или «от первого адреса»")
        origin_label = "из офиса"

    points = [origin] + [(float(stop.site.latitude), float(stop.site.longitude)) for stop in waiting]
    grid = matrix.build(points, departure)

    service = [service_seconds(first_fixed, options) if first_fixed else 0]
    service += [service_seconds(stop, options) for stop in waiting]
    windows: list[tuple[int | None, int | None]] = [(None, None)]
    windows += [(_offset(departure, stop.window_start), _offset(departure, stop.window_end)) for stop in waiting]

    order = _solve(grid.seconds, service, windows, 0 if options.return_to_start else None) if waiting else []

    # Пересчёт времени по найденному порядку — тем же правилам, что и в решателе.
    day_end = _offset(departure, options.day_end)
    timeline = []
    clock = service[0]
    meters = 0
    drive_total = 0
    previous = 0
    if first_fixed:
        timeline.append({"stop": first_fixed, "arrive": 0, "wait": 0, "late": 0, "drive": 0, "meters": 0})
    for node in order:
        drive = grid.seconds[previous][node]
        arrive = clock + drive
        start, finish = windows[node]
        wait = max(0, (start or 0) - arrive) if start is not None else 0
        arrive += wait
        late = max(0, arrive - finish) if finish is not None else 0
        timeline.append({
            "stop": waiting[node - 1], "arrive": arrive, "wait": wait, "late": late, "drive": drive,
            "meters": grid.meters[previous][node],
        })
        drive_total += drive
        meters += grid.meters[previous][node]
        clock = arrive + service[node]
        previous = node
    back = 0
    if options.return_to_start and order:
        back = grid.seconds[previous][0]
        drive_total += back
        meters += grid.meters[previous][0]
    finish_at = departure + dt.timedelta(seconds=clock + back)

    rows = []
    for item in timeline:
        stop = item["stop"]
        eta = departure + dt.timedelta(seconds=item["arrive"])
        rows.append({
            "id": stop.pk,
            "address": stop.site.address,
            "eta": eta.astimezone(tz).isoformat(),
            "leave": (eta + dt.timedelta(seconds=service_seconds(stop, options))).astimezone(tz).isoformat(),
            "drive_minutes": round(item["drive"] / 60),
            "km": round(item["meters"] / 1000, 1),
            "wait_minutes": round(item["wait"] / 60),
            "late_minutes": round(item["late"] / 60),
            "after_day_end": day_end is not None and item["arrive"] > day_end,
            "window": [t.strftime("%H:%M") if t else None for t in (stop.window_start, stop.window_end)],
        })

    summary = {
        "planned_at": now.astimezone(tz).isoformat(),
        "departure": departure.astimezone(tz).isoformat(),
        "finish": finish_at.astimezone(tz).isoformat(),
        "origin": origin_label,
        "origin_point": list(origin),
        "return_to_start": options.return_to_start,
        "provider": grid.provider,
        "provider_label": grid.label,
        "traffic": grid.traffic,
        "notes": grid.notes + ([f"{len(blind)} адр. без точки на карте не вошли в маршрут"] if blind else []),
        "drive_minutes": round(drive_total / 60),
        "km": round(meters / 1000, 1),
        "stops": len(rows),
        "late": sum(1 for row in rows if row["late_minutes"] > 0),
        "after_day_end": sum(1 for row in rows if row["after_day_end"]),
        "service_minutes": options.service_minutes,
        "per_instrument_minutes": options.per_instrument_minutes,
        "day_end": options.day_end.strftime("%H:%M"),
    }

    if apply:
        _apply(trip, finished, [item["stop"] for item in timeline], blind, rows, summary)
    return {"summary": summary, "rows": rows}


@transaction.atomic
def _apply(trip: Trip, finished, route, blind, rows, summary) -> None:
    by_id = {row["id"]: row for row in rows}
    position = 0
    for stop in [*finished, *route, *blind]:
        position += 1
        row = by_id.get(stop.pk)
        stop.position = position
        stop.eta = dt.datetime.fromisoformat(row["eta"]) if row else None
        stop.drive_seconds = row["drive_minutes"] * 60 if row else None
        stop.save(update_fields=["position", "eta", "drive_seconds"])
    trip.route = summary
    trip.save(update_fields=["route"])
