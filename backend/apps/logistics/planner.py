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
    day_start: dt.time = dt.time(8, 0)
    day_end: dt.time = dt.time(17, 0)
    return_to_start: bool = False
    # Командировка на несколько дней: адреса, что не влезли в рабочий день, уходят
    # на следующий — с утра от места, где закончили (ночуем рядом, а не в Кирове).
    last_day: dt.date | None = None


def work_day() -> tuple[dt.time, dt.time]:
    """Рабочий день по умолчанию — из настроек (WORK_DAY_START / WORK_DAY_END)."""
    def parse(value: str, fallback: dt.time) -> dt.time:
        try:
            return dt.time.fromisoformat(value)
        except (TypeError, ValueError):
            return fallback

    return (
        parse(getattr(settings, "WORK_DAY_START", "08:00"), dt.time(8, 0)),
        parse(getattr(settings, "WORK_DAY_END", "17:00"), dt.time(17, 0)),
    )


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
    local_departure = departure.astimezone(tz)
    day = local_departure.date()
    last_day = options.last_day if options.last_day and options.last_day > day else day

    def at(date: dt.date, time: dt.time) -> dt.datetime:
        return timezone.make_aware(dt.datetime.combine(date, time), tz)

    # День за днём: решатель раскладывает все оставшиеся адреса от текущего места
    # с окнами на этот день; что не влезает в рабочий день — на следующий, утром
    # от последнего адреса (ночуем рядом). В последний день берём всё, что осталось.
    timeline = []
    clock = departure + dt.timedelta(seconds=service[0])
    meters = 0
    drive_total = 0
    current = 0
    days_used = {day}
    if first_fixed:
        timeline.append({"stop": first_fixed, "arrive": departure, "day": day, "wait": 0, "late": 0, "drive": 0, "meters": 0})
    remaining = list(range(1, len(points)))
    while remaining:
        sub = [current, *remaining]
        seconds = [[grid.seconds[a][b] for b in sub] for a in sub]
        sub_service = [0] + [service[node] for node in remaining]

        def offset(time: dt.time | None) -> int | None:
            return None if time is None else int((at(day, time) - clock).total_seconds())

        windows = [(None, None)] + [
            (offset(waiting[node - 1].window_start), offset(waiting[node - 1].window_end)) for node in remaining
        ]
        final_day = day >= last_day
        back_home = 0 if options.return_to_start and final_day and current == 0 else None
        order = [sub[i] for i in _solve(seconds, sub_service, windows, back_home)]
        taken = []
        for node in order:
            stop = waiting[node - 1]
            drive = grid.seconds[current][node]
            arrive = clock + dt.timedelta(seconds=drive)
            wait = 0
            if stop.window_start and arrive < at(day, stop.window_start):
                wait = int((at(day, stop.window_start) - arrive).total_seconds())
                arrive = at(day, stop.window_start)
            done = arrive + dt.timedelta(seconds=service[node])
            if not final_day and done > at(day, options.day_end) and (taken or timeline):
                break  # не успеваем сегодня — этот и дальше на следующий день
            late = int((arrive - at(day, stop.window_end)).total_seconds()) if stop.window_end and arrive > at(day, stop.window_end) else 0
            timeline.append({
                "stop": stop, "arrive": arrive, "day": day, "wait": wait, "late": late, "drive": drive,
                "meters": grid.meters[current][node],
            })
            drive_total += drive
            meters += grid.meters[current][node]
            clock = done
            current = node
            taken.append(node)
        remaining = [node for node in remaining if node not in taken]
        if remaining:
            day = day + dt.timedelta(days=1)
            days_used.add(day)
            clock = at(day, options.day_start)
    back = 0
    if options.return_to_start and current:
        back = grid.seconds[current][0]
        drive_total += back
        meters += grid.meters[current][0]
    finish_at = clock + dt.timedelta(seconds=back)

    rows = []
    for item in timeline:
        stop = item["stop"]
        eta = item["arrive"]
        rows.append({
            "id": stop.pk,
            "address": stop.site.address,
            "day": item["day"].isoformat(),
            "eta": eta.astimezone(tz).isoformat(),
            "leave": (eta + dt.timedelta(seconds=service_seconds(stop, options))).astimezone(tz).isoformat(),
            "drive_minutes": round(item["drive"] / 60),
            "km": round(item["meters"] / 1000, 1),
            "wait_minutes": round(item["wait"] / 60),
            "late_minutes": round(item["late"] / 60),
            # Не успеваем закончить на адресе до конца рабочего дня.
            "after_day_end": eta + dt.timedelta(seconds=service_seconds(stop, options)) > at(item["day"], options.day_end),
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
        "day_start": options.day_start.strftime("%H:%M"),
        "day_end": options.day_end.strftime("%H:%M"),
        "days": sorted(d.isoformat() for d in days_used),
        "multi_day": bool(options.last_day),
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
        fields = ["position", "eta", "drive_seconds"]
        if row:
            stop.planned_date = dt.date.fromisoformat(row["day"])
            fields.append("planned_date")
            if stop.work_order_id:
                type(stop.work_order).objects.filter(pk=stop.work_order_id).update(scheduled_date=stop.planned_date)
        stop.save(update_fields=fields)
    trip.route = summary
    trip.save(update_fields=["route"])
