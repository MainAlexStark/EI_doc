"""Командировки: спрос, план маршрута, брони эталонов, план/факт, пакет на выезд."""

from __future__ import annotations

import datetime as dt
import io
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, OuterRef, Q, Subquery
from django.utils import timezone

from apps.logistics import routing
from apps.logistics.models import StandardReservation, StopStatus, Trip, TripStatus, TripStop
from apps.verification.models import Instrument, Verification, VerificationStatus, WorkOrder, WorkOrderStatus


class TripError(ValueError):
    """Сообщение для человека."""


def _f(value) -> float | None:
    return float(value) if value is not None else None


def depot(trip: Trip | None = None) -> tuple[float, float] | None:
    if trip and trip.start_latitude is not None and trip.start_longitude is not None:
        return float(trip.start_latitude), float(trip.start_longitude)
    lat = getattr(settings, "LOGISTICS_DEPOT_LAT", None)
    lon = getattr(settings, "LOGISTICS_DEPOT_LON", None)
    if lat in (None, "") or lon in (None, ""):
        return None
    return float(lat), float(lon)


# ---------------------------------------------------------------------------
# Спрос
# ---------------------------------------------------------------------------
def expiring_instruments(date_from: dt.date, date_to: dt.date):
    """Экземпляры, у которых последняя годная поверка истекает в окне."""
    last = (
        Verification.objects.filter(instrument=OuterRef("pk"))
        .exclude(status=VerificationStatus.DRAFT)
        .order_by("-verified_at")
    )
    return (
        Instrument.objects.annotate(
            last_next=Subquery(last.values("next_verification_date")[:1]),
            last_suitable=Subquery(last.values("suitable")[:1]),
        )
        .filter(last_suitable=True, last_next__gte=date_from, last_next__lte=date_to)
        .select_related("site__district", "si_type", "owner")
    )


def open_work_orders():
    """Наряды без командировки, которые ещё предстоит выполнить."""
    return (
        WorkOrder.objects.filter(status=WorkOrderStatus.PLANNED, trip_stop__isnull=True)
        .select_related("site__district", "client", "assigned_employee")
    )


def demand(days: int = 60, today: dt.date | None = None) -> list[dict]:
    """Накопленный спрос по районам: открытые наряды, новые заявки, истекающие МПИ."""
    from apps.hub.models import Request, RequestStatus

    today = today or timezone.localdate()
    until = today + dt.timedelta(days=days)
    groups: dict[str, dict] = defaultdict(lambda: {
        "district_id": None, "district": "", "work_orders": 0, "requests": 0, "expiring": 0,
        "points": [], "earliest": None,
    })

    def bucket(district):
        key = str(district.pk) if district else "-"
        group = groups[key]
        group["district_id"] = district.pk if district else None
        group["district"] = district.name if district else "Район не определён"
        return group

    def add_point(group, kind, label, lat, lon, when=None, ident=None):
        group["points"].append({"kind": kind, "id": ident, "label": label, "lat": _f(lat), "lon": _f(lon),
                                "date": when})
        if when and (group["earliest"] is None or when < group["earliest"]):
            group["earliest"] = when

    for wo in open_work_orders():
        group = bucket(wo.site.district)
        group["work_orders"] += 1
        add_point(group, "work_order", f"Наряд №{wo.pk} · {wo.site.address}",
                  wo.site.latitude, wo.site.longitude, wo.scheduled_date, wo.pk)

    for req in Request.objects.filter(status__in=[RequestStatus.NEW, RequestStatus.ROUTED]).select_related("district"):
        group = bucket(req.district)
        group["requests"] += 1
        add_point(group, "request", f"Заявка №{req.pk} · {req.address}", req.latitude, req.longitude,
                  req.desired_date, req.pk)

    for inst in expiring_instruments(today, until):
        site = inst.site
        group = bucket(site.district if site else None)
        group["expiring"] += 1
        add_point(group, "expiring", f"{inst.si_type.name} № {inst.serial_number} · {site or 'адрес не указан'}",
                  site.latitude if site else None, site.longitude if site else None, inst.last_next, inst.pk)

    result = []
    for group in groups.values():
        group["total"] = group["work_orders"] + group["requests"] + group["expiring"]
        located = [p for p in group["points"] if p["lat"] is not None]
        if located:
            group["center"] = {"lat": sum(p["lat"] for p in located) / len(located),
                               "lon": sum(p["lon"] for p in located) / len(located)}
        else:
            group["center"] = None
        result.append(group)
    result.sort(key=lambda g: (-g["total"], g["district"]))
    return result


# ---------------------------------------------------------------------------
# Командировка
# ---------------------------------------------------------------------------
@transaction.atomic
def add_work_orders(trip: Trip, work_order_ids: list[int]) -> list[TripStop]:
    created = []
    taken = set(TripStop.objects.filter(work_order_id__in=work_order_ids).values_list("work_order_id", flat=True))
    position = (trip.stops.aggregate(m=Max("position"))["m"] or 0)
    for wo in WorkOrder.objects.filter(pk__in=work_order_ids).select_related("site"):
        if wo.pk in taken:
            continue
        position += 1
        day = wo.scheduled_date if wo.scheduled_date and trip.date_from <= wo.scheduled_date <= trip.date_to else None
        created.append(TripStop.objects.create(
            trip=trip, work_order=wo, address=wo.site.address,
            latitude=wo.site.latitude, longitude=wo.site.longitude,
            day=day, position=position,
            window_from=wo.scheduled_time,
            window_to=(dt.datetime.combine(dt.date(2000, 1, 1), wo.scheduled_time) + dt.timedelta(hours=1)).time()
            if wo.scheduled_time else None,
        ))
        if wo.assigned_employee_id:
            trip.employees.add(wo.assigned_employee_id)
    return created


def trip_days(trip: Trip) -> list[dt.date]:
    return [trip.date_from + dt.timedelta(days=i) for i in range((trip.date_to - trip.date_from).days + 1)]


def _day_capacity() -> int:
    return int(getattr(settings, "LOGISTICS_DAY_MINUTES", 9 * 60))


def _day_start() -> dt.time:
    raw = str(getattr(settings, "LOGISTICS_DAY_START", "09:00"))
    hour, minute = raw.split(":")
    return dt.time(int(hour), int(minute))


@transaction.atomic
def optimize(trip: Trip) -> dict:
    """Разложить остановки по дням и упорядочить каждый день.

    Остановки с назначенным днём остаются в нём. Остальные идут в общий
    порядок объезда (маршрут-«развёртка» из депо) и режутся на дни по
    вместимости рабочего дня — соседние по маршруту объекты попадают в один
    день. Затем каждый день решается отдельно с окнами и ETA.
    """
    start = depot(trip)
    stops = list(trip.stops.exclude(status=StopStatus.SKIPPED))
    if not stops:
        raise TripError("В командировке нет остановок")
    located = [s for s in stops if s.has_coordinates]
    if start is None:
        if not located:
            raise TripError("Нет координат ни у одной остановки — нечего считать")
        start = (float(located[0].latitude), float(located[0].longitude))
    unlocated = [s for s in stops if not s.has_coordinates]

    days = trip_days(trip)
    capacity = _day_capacity()
    day_start = _day_start()

    floating = [s for s in located if s.day is None or s.day not in days]
    if floating:
        points = [routing.Point(s.pk, float(s.latitude), float(s.longitude), s.window_from, s.window_to,
                                s.service_minutes) for s in floating]
        sweep = routing.plan_route(start, points, day_start=day_start, day_minutes=10**6)
        fixed_load = defaultdict(float)
        for s in located:
            if s.day in days and s not in floating:
                fixed_load[s.day] += s.service_minutes + 15
        day_index, load = 0, fixed_load[days[0]]
        for idx in sweep.order:
            stop = floating[idx]
            cost = stop.service_minutes + 20
            while load + cost > capacity and day_index < len(days) - 1:
                day_index += 1
                load = fixed_load[days[day_index]]
            stop.day = days[day_index]
            load += cost

    summary = {"days": [], "warnings": [], "unlocated": [s.address for s in unlocated]}
    for day in days:
        todays = [s for s in located if s.day == day]
        if not todays:
            continue
        points = [routing.Point(s.address, float(s.latitude), float(s.longitude), s.window_from, s.window_to,
                                s.service_minutes) for s in todays]
        route = routing.plan_route(start, points, day_start=day_start, day_minutes=capacity)
        for position, (idx, minutes) in enumerate(zip(route.order, route.minutes), start=1):
            stop = todays[idx]
            stop.position = position
            stop.eta = routing.eta(day_start, minutes)
            stop.save(update_fields=["day", "position", "eta"])
        summary["days"].append({
            "day": day, "stops": len(todays), "minutes": round(route.total_minutes),
            "km": round(route.total_km, 1) if route.total_km is not None else None,
            "source": route.source, "solver": route.solver,
        })
        summary["warnings"] += [f"{day:%d.%m}: {w}" for w in route.warnings]
    for position, stop in enumerate(unlocated, start=900):
        stop.position = position
        stop.save(update_fields=["position"])
    if unlocated:
        summary["warnings"].append(f"без координат, не в маршруте: {len(unlocated)}")
    return summary


@transaction.atomic
def reserve(trip: Trip, standard_ids: list[int]) -> list[dict]:
    """Забронировать эталоны на даты командировки. Возвращает отказы с причиной."""
    from apps.catalog.models import Standard

    refused = []
    for standard in Standard.objects.filter(pk__in=standard_ids):
        reservation = StandardReservation.objects.filter(trip=trip, standard=standard).first() or \
            StandardReservation(trip=trip, standard=standard)
        reservation.date_from, reservation.date_to = trip.date_from, trip.date_to
        try:
            reservation.full_clean(exclude=["trip", "standard"])
        except ValidationError as exc:
            refused.append({"standard": standard.pk, "name": str(standard), "detail": "; ".join(exc.messages)})
            continue
        reservation.save()
    return refused


def plan_fact(trip: Trip) -> dict:
    stops = list(trip.stops.select_related("work_order"))
    planned = len(stops)
    done = sum(1 for s in stops if s.status == StopStatus.DONE
               or (s.work_order and s.work_order.status == WorkOrderStatus.DONE))
    skipped = sum(1 for s in stops if s.status == StopStatus.SKIPPED
                  or (s.work_order and s.work_order.status == WorkOrderStatus.CANCELLED))
    wo_ids = [s.work_order_id for s in stops if s.work_order_id]
    verifications = Verification.objects.filter(work_order_id__in=wo_ids)
    return {
        "stops_planned": planned,
        "stops_done": done,
        "stops_skipped": skipped,
        "stops_left": planned - done - skipped,
        "verifications": verifications.count(),
        "verifications_accepted": verifications.filter(status=VerificationStatus.ACCEPTED).count(),
        "unsuitable": verifications.filter(suitable=False).count(),
        "budget": trip.budget,
        "actual_cost": trip.actual_cost,
        "cost_per_verification": (
            (trip.actual_cost / verifications.count()).quantize(Decimal("0.01"))
            if trip.actual_cost and verifications.count() else None
        ),
    }


def refresh_stop_statuses(trip: Trip) -> None:
    """Подтянуть факт по нарядам: закрытый наряд — остановка выполнена."""
    for stop in trip.stops.select_related("work_order").filter(status=StopStatus.PLANNED, work_order__isnull=False):
        if stop.work_order.status == WorkOrderStatus.DONE:
            stop.status = StopStatus.DONE
            stop.save(update_fields=["status"])
        elif stop.work_order.status == WorkOrderStatus.CANCELLED:
            stop.status = StopStatus.SKIPPED
            stop.save(update_fields=["status"])
    if trip.status == TripStatus.IN_PROGRESS and not trip.stops.filter(status=StopStatus.PLANNED).exists():
        trip.status = TripStatus.DONE
        trip.save(update_fields=["status"])


# ---------------------------------------------------------------------------
# Пакет на выезд
# ---------------------------------------------------------------------------
def pack_context(trip: Trip) -> dict:
    days = defaultdict(list)
    for stop in trip.stops.select_related("work_order__client", "work_order__site").order_by("day", "position"):
        site = stop.work_order.site if stop.work_order else None
        days[stop.day.strftime("%d.%m.%Y") if stop.day else "без дня"].append({
            "position": stop.position,
            "eta": stop.eta.strftime("%H:%M") if stop.eta else "",
            "window": f"{stop.window_from:%H:%M}" if stop.window_from else "",
            "address": stop.address,
            "client": str(stop.work_order.client) if stop.work_order else "",
            "contact": site.contact if site else "",
            "access": site.access_note if site else "",
            "restricted": bool(site and site.is_restricted),
            "work_order": stop.work_order_id or "",
        })
    return {
        "title": trip.title,
        "dates": f"{trip.date_from:%d.%m.%Y} — {trip.date_to:%d.%m.%Y}",
        "employees": [e.full_name for e in trip.employees.all()],
        "vehicle": trip.vehicle,
        "note": trip.note,
        "standards": [
            {"name": r.standard.name, "number": r.standard.fif_number, "serial": r.standard.serial_number,
             "valid_until": r.standard.valid_until.strftime("%d.%m.%Y")}
            for r in trip.reservations.select_related("standard")
        ],
        "days": [{"day": day, "stops": stops} for day, stops in days.items()],
        "generated": timezone.localtime().strftime("%d.%m.%Y %H:%M"),
    }


def render_pack(trip: Trip) -> bytes:
    """Маршрутный лист + бланки на режимные объекты одним PDF."""
    from pypdf import PdfReader, PdfWriter

    from apps.verification.render import render_blank, render_pdf

    template = Path(__file__).parent / "typst" / "trip_pack.typ"
    sheet = render_pdf(pack_context(trip), template=template)
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(sheet)).pages:
        writer.add_page(page)
    restricted = trip.stops.filter(work_order__site__is_restricted=True).select_related("work_order")
    for stop in restricted:
        blank = render_blank(stop.work_order, copies=2)
        for page in PdfReader(io.BytesIO(blank)).pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def trips_for(user):
    """Поверитель видит свои командировки, диспетчер — все."""
    qs = Trip.objects.prefetch_related("employees")
    if user.has_perm("logistics.plan_trips"):
        return qs
    employee = getattr(user, "employee", None)
    return qs.filter(Q(employees=employee)).distinct() if employee else qs.none()
