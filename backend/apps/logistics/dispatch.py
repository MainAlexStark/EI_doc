"""Диспетчерская: вся работа фирмы на одном экране и быстрое назначение.

Экраны руководства (старший метролог, менеджер, руководитель, владелец) —
«Панель», карта «Выездов», таблица «Приёмок», командный «График» — и
инструменты ассистента ходят сюда, а не каждый в свои модели: правила
назначения одни и те же, где бы человек ни нажал кнопку.

Что значит «назначить»:

* адрес дня в городе (``Trip.kind == city``) — переезжает в день другого
  поверителя (или на другую дату), наряд следом меняет исполнителя и дату;
  опустевший автоматический день удаляется, чтобы не висел пустым;
* адрес командировки — исполнитель наряда внутри той же командировки; кого
  не было в составе, туда добавляем — пропуска и маршрут считаются по составу;
* наряд на выезд без даты — исполнитель и (если дали) дата: с датой сразу
  встаёт в день поверителя (``attach_to_city_day``);
* приёмка (заказ, приборы привезли сами) — просто исполнитель;
* заявка с сайта — подтверждение с исполнителем и датой (как в «Заявках»).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.core.models import Employee, Role
from apps.logistics.models import StopStatus, Trip, TripKind, TripStatus, TripStop
from apps.logistics.service import TripError, attach_to_city_day, city_day_for
from apps.verification.models import (
    Verification,
    VerificationStatus,
    WorkOrder,
    WorkOrderKind,
    WorkOrderStatus,
)

#: Кто видит всю фирму и назначает работу. Совпадает с logistics.api.MANAGER_ROLES.
CONTROL_ROLES = {Role.OWNER, Role.HEAD, Role.METROLOGIST, Role.MANAGER}
OPEN_TRIP = (TripStatus.PLANNED, TripStatus.ACTIVE)
OPEN_ORDER = (WorkOrderStatus.PLANNED, WorkOrderStatus.IN_PROGRESS)
MAX_SPAN_DAYS = 62


class DispatchError(ValueError):
    """Назначение невозможно по смыслу — текст показывается человеку как есть."""


def can_control(user) -> bool:
    return bool(getattr(user, "is_superuser", False) or getattr(user, "role", "") in CONTROL_ROLES)


def short_name(full_name: str) -> str:
    last, *rest = (full_name or "").split() or [""]
    return " ".join([last, *(f"{part[0]}." for part in rest if part)])


def active_employees():
    return Employee.objects.filter(is_active=True).select_related("user").order_by("full_name")


def _t(value: dt.time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def _f(value) -> float | None:
    return float(value) if value is not None else None


def span(date_from: dt.date, date_to: dt.date) -> list[dt.date]:
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days >= MAX_SPAN_DAYS:
        date_to = date_from + dt.timedelta(days=MAX_SPAN_DAYS - 1)
    return [date_from + dt.timedelta(days=i) for i in range((date_to - date_from).days + 1)]


# ===========================================================================
# Назначение
# ===========================================================================
@dataclass
class Assigned:
    type: str
    id: int
    text: str
    trip_id: int | None = None


def _renumber(trip: Trip) -> None:
    for index, stop in enumerate(trip.stops.order_by("position", "id"), start=1):
        if stop.position != index:
            TripStop.objects.filter(pk=stop.pk).update(position=index)


def _drop_if_empty_city_day(trip: Trip) -> None:
    """Автоматический день поверителя без адресов никому не нужен."""
    if trip.kind == TripKind.CITY and trip.status == TripStatus.PLANNED and not trip.stops.exists():
        trip.delete()


@transaction.atomic
def assign_stop(stop: TripStop, employee: Employee, date: dt.date | None = None) -> Assigned:
    if stop.status != StopStatus.PLANNED:
        raise DispatchError(f"{stop.site.address}: адрес уже отмечен ({stop.get_status_display().lower()}) — не переназначается")
    trip = stop.trip
    if not trip.is_open:
        raise DispatchError(f"{stop.site.address}: выезд закрыт")
    order = stop.work_order
    if order is not None and order.verifications.exclude(verifier=employee).exists():
        raise DispatchError(f"{stop.site.address}: по адресу уже есть поверки другого поверителя")

    if trip.kind == TripKind.CITY:
        target_date = date or stop.planned_date or trip.date_start or timezone.localdate()
        target = city_day_for(employee, target_date)
        if target.pk != trip.pk:
            last = target.stops.aggregate(last=Max("position"))["last"] or 0
            stop.trip = target
            stop.position = last + 1
            stop.eta = None
            stop.drive_seconds = None
        stop.planned_date = target_date
        stop.save(update_fields=["trip", "position", "eta", "drive_seconds", "planned_date"])
        if order is not None:
            order.assigned_employee = employee
            order.scheduled_date = target_date
            order.save(update_fields=["assigned_employee", "scheduled_date"])
        if target.pk != trip.pk:
            _renumber(trip)
            _drop_if_empty_city_day(trip)
        return Assigned("stop", stop.pk, f"{stop.site.address} → {employee.full_name}, {target_date:%d.%m}", target.pk)

    # Командировка: исполнитель внутри той же командировки, дата — в её пределах.
    if date and trip.date_start and trip.date_end and not (trip.date_start <= date <= trip.date_end):
        raise DispatchError(
            f"{stop.site.address}: {date:%d.%m} вне дат командировки "
            f"({trip.date_start:%d.%m}–{trip.date_end:%d.%m})"
        )
    if employee.pk != trip.lead_id and not trip.members.filter(pk=employee.pk).exists():
        trip.members.add(employee)
    if date:
        stop.planned_date = date
        stop.save(update_fields=["planned_date"])
    if order is not None:
        order.assigned_employee = employee
        if date:
            order.scheduled_date = date
        order.save(update_fields=["assigned_employee", "scheduled_date"])
    return Assigned("stop", stop.pk, f"{stop.site.address} → {employee.full_name} ({trip.title})", trip.pk)


@transaction.atomic
def assign_order(
    order: WorkOrder, employee: Employee, date: dt.date | None = None, time: dt.time | None = None,
) -> Assigned:
    if order.is_closed:
        raise DispatchError(f"Наряд №{order.pk} закрыт")
    stop = TripStop.objects.filter(work_order=order).select_related("trip", "site").first()
    if stop is not None:
        result = assign_stop(stop, employee, date)
        if time:
            stop.refresh_from_db()
            from apps.logistics.service import window_for

            stop.window_start, stop.window_end = window_for(time)
            stop.save(update_fields=["window_start", "window_end"])
        return Assigned("order", order.pk, result.text, result.trip_id)
    if order.kind == WorkOrderKind.LAB:
        # Приёмку бывает поверяют вдвоём: исполнитель тут — ответственный, поверки остаются у своих.
        order.assigned_employee = employee
        order.save(update_fields=["assigned_employee"])
        return Assigned("order", order.pk, f"Приёмка «{order.client.name}» → {employee.full_name}")
    order.assigned_employee = employee
    fields = ["assigned_employee"]
    if date:
        order.scheduled_date = date
        fields.append("scheduled_date")
    if time:
        order.scheduled_time = time
        fields.append("scheduled_time")
    order.save(update_fields=fields)
    attached = attach_to_city_day(order)
    when = f", {order.scheduled_date:%d.%m}" if order.scheduled_date else " (без даты)"
    return Assigned("order", order.pk, f"{order.site.address} → {employee.full_name}{when}",
                    attached.trip_id if attached else None)


def assign_request(request_obj, employee: Employee, date: dt.date | None = None,
                   time: dt.time | None = None) -> Assigned:
    from apps.hub import service as hub_service
    from apps.hub.models import RequestStatus

    if request_obj.status not in (RequestStatus.NEW, RequestStatus.ROUTED):
        raise DispatchError(f"Заявка №{request_obj.pk}: уже {request_obj.get_status_display().lower()}")
    try:
        order = hub_service.confirm_request(request_obj, employee=employee, scheduled_date=date, scheduled_time=time)
    except hub_service.RequestActionError as exc:
        raise DispatchError(str(exc)) from exc
    stop = TripStop.objects.filter(work_order=order).first()
    when = f", {order.scheduled_date:%d.%m}" if order.scheduled_date else " (без даты — назначьте день)"
    return Assigned("request", request_obj.pk, f"Заявка №{request_obj.pk} → {employee.full_name}{when}",
                    stop.trip_id if stop else None)


def assign_many(items: list[dict], employee: Employee, date: dt.date | None = None,
                time: dt.time | None = None) -> dict:
    """Пачкой: [{type: stop|order|request, id}] — что вышло и что нет (по каждому своя причина)."""
    from apps.hub.models import Request

    done: list[dict] = []
    errors: list[dict] = []
    models = {"stop": TripStop, "order": WorkOrder, "request": Request}
    for item in items[:100]:
        kind, pk = item.get("type"), item.get("id")
        model = models.get(kind)
        obj = model.objects.filter(pk=pk).first() if model else None
        if obj is None:
            errors.append({"type": kind, "id": pk, "error": "Не найдено"})
            continue
        try:
            if kind == "stop":
                result = assign_stop(obj, employee, date)
            elif kind == "order":
                result = assign_order(obj, employee, date, time)
            else:
                result = assign_request(obj, employee, date, time)
            done.append(result.__dict__)
        except (DispatchError, TripError) as exc:
            errors.append({"type": kind, "id": pk, "error": str(exc)})
    return {"done": done, "errors": errors}


# ===========================================================================
# Что видит руководство
# ===========================================================================
def _trips_on(day: dt.date):
    return Trip.objects.filter(status__in=OPEN_TRIP).filter(
        Q(date_start__lte=day, date_end__gte=day) | Q(date_start=day, date_end__isnull=True)
    )


def overview(day: dt.date | None = None) -> dict:
    """Панель управления: итоги по фирме, что требует внимания, карточка каждого сотрудника на день."""
    from apps.hub.models import Request, RequestStatus
    from apps.tasks.models import Task, TaskStatus

    day = day or timezone.localdate()
    today = timezone.localdate()
    employees = list(active_employees())
    ids = [e.pk for e in employees]

    stops = (
        TripStop.objects.filter(Q(planned_date=day) | Q(planned_date__isnull=True, trip__date_start=day))
        .filter(trip__status__in=OPEN_TRIP)
        .select_related("work_order", "trip")
    )
    by_emp_stops: dict[int, dict] = defaultdict(lambda: {"total": 0, "done": 0, "waiting": 0, "missed": 0, "trips": set()})
    for stop in stops:
        emp_id = stop.work_order.assigned_employee_id if stop.work_order_id else stop.trip.lead_id
        row = by_emp_stops[emp_id]
        row["total"] += 1
        row["trips"].add(stop.trip_id)
        if stop.status == StopStatus.DONE:
            row["done"] += 1
        elif stop.status == StopStatus.PLANNED:
            row["waiting"] += 1
        else:
            row["missed"] += 1

    trips_today = list(_trips_on(day).exclude(kind=TripKind.CITY).select_related("lead").prefetch_related("members"))
    emp_trips: dict[int, list[dict]] = defaultdict(list)
    for trip in trips_today:
        brief = {"id": trip.pk, "title": trip.title, "kind": trip.kind, "destination": trip.destination,
                 "date_start": trip.date_start, "date_end": trip.date_end, "is_lead": False}
        emp_trips[trip.lead_id].append({**brief, "is_lead": True})
        for member in trip.members.all():
            emp_trips[member.pk].append(brief)
    city_trip = {t.lead_id: t.pk for t in Trip.objects.filter(kind=TripKind.CITY, date_start=day).exclude(status=TripStatus.CANCELLED)}

    lab = WorkOrder.objects.filter(kind=WorkOrderKind.LAB, issued_at__isnull=True).exclude(status=WorkOrderStatus.CANCELLED)
    lab_by = dict(lab.values_list("assigned_employee").annotate(n=Count("id")))
    lab_ready = [
        o for o in lab.annotate(
            total=Count("verifications"),
            ready=Count("verifications", filter=Q(verifications__status__in=[VerificationStatus.READY, VerificationStatus.ACCEPTED])),
        ) if o.total and o.ready == o.total
    ]

    ver_stats = dict()
    for emp_id, status, n in (
        Verification.objects.filter(verifier_id__in=ids, status__in=[VerificationStatus.DRAFT, VerificationStatus.READY, VerificationStatus.REJECTED])
        .values_list("verifier_id", "status").annotate(n=Count("id"))
    ):
        ver_stats.setdefault(emp_id, {})[status] = n

    tasks = Task.objects.filter(status__in=[TaskStatus.TODO, TaskStatus.IN_PROGRESS])
    tasks_open = dict(tasks.values_list("assignee").annotate(n=Count("id")))
    tasks_overdue = dict(tasks.filter(due_date__lt=today).values_list("assignee").annotate(n=Count("id")))

    from apps.hub.models import EmployeeAvailability

    slots: dict[int, list[dict]] = defaultdict(list)
    for s in EmployeeAvailability.objects.filter(date=day, employee_id__in=ids).order_by("start_time"):
        slots[s.employee_id].append({"kind": s.kind, "start": _t(s.start_time), "end": _t(s.end_time), "is_priority": s.is_priority})

    week_end = day + dt.timedelta(days=6)
    week = dict(
        TripStop.objects.filter(planned_date__range=(day, week_end), status=StopStatus.PLANNED, trip__status__in=OPEN_TRIP)
        .values_list("work_order__assigned_employee").annotate(n=Count("id"))
    )

    cards = []
    for e in employees:
        s = by_emp_stops.get(e.pk, {"total": 0, "done": 0, "waiting": 0, "missed": 0, "trips": set()})
        v = ver_stats.get(e.pk, {})
        trips = emp_trips.get(e.pk, [])
        if trips:
            state = "trip"
        elif s["total"]:
            state = "city"
        elif slots.get(e.pk):
            state = "free"
        else:
            state = "off"
        cards.append({
            "id": e.pk, "full_name": e.full_name, "short_name": e.short_name or short_name(e.full_name),
            "position": e.position, "role": getattr(e.user, "role", "") if e.user_id else "",
            "state": state,
            "city_trip_id": city_trip.get(e.pk),
            "stops": {k: s[k] for k in ("total", "done", "waiting", "missed")},
            "trips": trips, "slots": slots.get(e.pk, []),
            "lab_open": lab_by.get(e.pk, 0),
            "drafts": v.get(VerificationStatus.DRAFT, 0), "ready": v.get(VerificationStatus.READY, 0),
            "rejected": v.get(VerificationStatus.REJECTED, 0),
            "tasks_open": tasks_open.get(e.pk, 0), "tasks_overdue": tasks_overdue.get(e.pk, 0),
            "week_stops": week.get(e.pk, 0),
        })

    new_requests = list(
        Request.objects.filter(status__in=[RequestStatus.NEW, RequestStatus.ROUTED])
        .select_related("suggested_employee").order_by("created_at")[:30]
    )
    unscheduled = list(
        WorkOrder.objects.filter(kind=WorkOrderKind.FIELD, status__in=OPEN_ORDER, trip_stop__isnull=True)
        .select_related("site", "client", "assigned_employee").order_by("created_at")[:30]
    )
    overdue_tasks = list(tasks.filter(due_date__lt=today).select_related("assignee").order_by("due_date")[:15])
    unlocated = (
        TripStop.objects.filter(trip__status__in=OPEN_TRIP, status=StopStatus.PLANNED, planned_date__gte=today)
        .filter(Q(site__latitude__isnull=True) | Q(site__longitude__isnull=True)).count()
    )

    attention = []
    for r in new_requests:
        attention.append({
            "type": "request", "id": r.pk, "title": f"Заявка №{r.pk}: {r.contact_name}",
            "subtitle": r.address, "date": r.desired_date, "time": _t(r.desired_time),
            "suggested_employee_id": r.suggested_employee_id,
            "suggested_employee": r.suggested_employee.full_name if r.suggested_employee_id else "",
        })
    for o in unscheduled:
        attention.append({
            "type": "order", "id": o.pk, "title": f"Выезд без даты: {o.site.address}",
            "subtitle": f"{o.client.name} · {o.assigned_employee.full_name}", "date": None, "time": None,
            "suggested_employee_id": o.assigned_employee_id, "suggested_employee": o.assigned_employee.full_name,
        })
    for o in lab_ready:
        attention.append({
            "type": "issue", "id": o.pk, "title": f"Готово к выдаче: {o.client.name}",
            "subtitle": f"{o.total} приб. · {o.assigned_employee.full_name}", "date": None, "time": None,
        })
    for t in overdue_tasks:
        attention.append({
            "type": "task", "id": t.pk, "title": f"Просрочена задача: {t.title}",
            "subtitle": t.assignee.full_name if t.assignee_id else "без исполнителя", "date": t.due_date, "time": None,
        })

    totals = {
        "stops_total": sum(c["stops"]["total"] for c in cards),
        "stops_done": sum(c["stops"]["done"] for c in cards),
        "stops_waiting": sum(c["stops"]["waiting"] for c in cards),
        "on_trip": sum(1 for c in cards if c["state"] == "trip"),
        "in_city": sum(1 for c in cards if c["state"] == "city"),
        "free": sum(1 for c in cards if c["state"] == "free"),
        "requests_new": len(new_requests),
        "unscheduled": len(unscheduled),
        "lab_open": lab.count(),
        "lab_ready": len(lab_ready),
        "review": Verification.objects.filter(status=VerificationStatus.READY).count(),
        "rejected": Verification.objects.filter(status=VerificationStatus.REJECTED).count(),
        "tasks_overdue": tasks.filter(due_date__lt=today).count(),
        "unlocated": unlocated,
    }
    return {"date": day, "totals": totals, "attention": attention, "employees": cards}


def map_points(date_from: dt.date, date_to: dt.date) -> dict:
    """Все адреса выездов за период (по дню адреса) + очередь неназначенного: заявки и выезды без даты."""
    from apps.hub.models import Request, RequestStatus

    days = span(date_from, date_to)
    date_from, date_to = days[0], days[-1]
    stops = (
        TripStop.objects.filter(trip__status__in=OPEN_TRIP)
        .filter(
            Q(planned_date__range=(date_from, date_to))
            | Q(planned_date__isnull=True, trip__date_start__lte=date_to, trip__date_end__gte=date_from)
        )
        .select_related("trip", "site", "client", "work_order__assigned_employee", "trip__lead")
        .order_by("planned_date", "trip_id", "position")
    )
    points, unlocated = [], []
    for stop in stops:
        emp = stop.work_order.assigned_employee if stop.work_order_id else stop.trip.lead
        row = {
            "type": "stop", "id": stop.pk, "trip_id": stop.trip_id, "trip_title": stop.trip.title,
            "trip_kind": stop.trip.kind, "position": stop.position, "status": stop.status,
            "status_display": stop.get_status_display(), "address": stop.site.address,
            "client": stop.client.name, "contact_name": stop.contact_name, "contact_phone": stop.contact_phone,
            "date": stop.planned_date or stop.trip.date_start, "window_start": _t(stop.window_start),
            "window_end": _t(stop.window_end), "planned_count": stop.planned_count,
            "employee_id": emp.pk, "employee": emp.full_name,
            "latitude": _f(stop.site.latitude), "longitude": _f(stop.site.longitude),
            "work_order_id": stop.work_order_id, "note": stop.note, "purpose": stop.purpose,
        }
        (points if row["latitude"] is not None and row["longitude"] is not None else unlocated).append(row)

    queue = []
    for r in (
        Request.objects.filter(status__in=[RequestStatus.NEW, RequestStatus.ROUTED])
        .select_related("suggested_employee").prefetch_related("items").order_by("desired_date", "created_at")[:100]
    ):
        queue.append({
            "type": "request", "id": r.pk, "address": r.address, "client": r.contact_name,
            "contact_phone": r.contact_phone, "date": r.desired_date, "window_start": _t(r.desired_time),
            "window_end": None, "planned_count": sum(i.quantity for i in r.items.all()) or None,
            "employee_id": r.suggested_employee_id,
            "employee": r.suggested_employee.full_name if r.suggested_employee_id else "",
            "latitude": _f(r.latitude), "longitude": _f(r.longitude), "note": r.comment[:200],
            "source": r.get_source_display(),
        })
    for o in (
        WorkOrder.objects.filter(kind=WorkOrderKind.FIELD, status__in=OPEN_ORDER, trip_stop__isnull=True)
        .select_related("site", "client", "assigned_employee").order_by("created_at")[:100]
    ):
        queue.append({
            "type": "order", "id": o.pk, "address": o.site.address, "client": o.client.name,
            "contact_phone": o.client.phone, "date": o.scheduled_date, "window_start": _t(o.scheduled_time),
            "window_end": None, "planned_count": None, "employee_id": o.assigned_employee_id,
            "employee": o.assigned_employee.full_name,
            "latitude": _f(o.site.latitude), "longitude": _f(o.site.longitude), "note": o.note[:200],
            "source": "Наряд",
        })
    trips = [
        {"id": t.pk, "title": t.title, "kind": t.kind, "lead_id": t.lead_id, "lead": t.lead.full_name,
         "date_start": t.date_start, "date_end": t.date_end, "status": t.status,
         "member_ids": [m.pk for m in t.members.all()]}
        for t in Trip.objects.filter(status__in=OPEN_TRIP, date_start__lte=date_to)
        .filter(Q(date_end__gte=date_from) | Q(date_end__isnull=True, date_start__gte=date_from))
        .exclude(kind=TripKind.CITY).select_related("lead").prefetch_related("members")
    ]
    employees = [{"id": e.pk, "full_name": e.full_name, "short_name": e.short_name or short_name(e.full_name)}
                 for e in active_employees()]
    return {"date_from": date_from, "date_to": date_to, "points": points, "unlocated": unlocated,
            "queue": queue, "trips": trips, "employees": employees}


def team_schedule(date_from: dt.date, date_to: dt.date) -> dict:
    """Сотрудники × дни: когда может выезжать, сколько адресов, в командировке ли, задачи со сроком."""
    from apps.hub.models import EmployeeAvailability
    from apps.tasks.models import Task, TaskStatus

    days = span(date_from, date_to)
    date_from, date_to = days[0], days[-1]
    employees = list(active_employees())
    cells: dict[tuple[int, dt.date], dict] = defaultdict(
        lambda: {"slots": [], "stops": 0, "done": 0, "trips": [], "tasks": 0, "lab": 0}
    )
    for s in EmployeeAvailability.objects.filter(date__range=(date_from, date_to)).order_by("start_time"):
        cells[(s.employee_id, s.date)]["slots"].append({
            "id": s.pk, "kind": s.kind, "start": _t(s.start_time), "end": _t(s.end_time),
            "is_priority": s.is_priority, "note": s.note,
        })
    for emp_id, day, status, n in (
        TripStop.objects.filter(planned_date__range=(date_from, date_to), trip__status__in=OPEN_TRIP)
        .values_list("work_order__assigned_employee", "planned_date", "status").annotate(n=Count("id"))
    ):
        if emp_id is None:
            continue
        cell = cells[(emp_id, day)]
        cell["stops"] += n
        if status == StopStatus.DONE:
            cell["done"] += n
    for trip in (
        Trip.objects.filter(status__in=OPEN_TRIP, date_start__lte=date_to, date_end__gte=date_from)
        .exclude(kind=TripKind.CITY).prefetch_related("members")
    ):
        people = [trip.lead_id, *[m.pk for m in trip.members.all()]]
        for day in days:
            if trip.date_start <= day <= trip.date_end:
                for pk in people:
                    cells[(pk, day)]["trips"].append({"id": trip.pk, "title": trip.title, "kind": trip.kind,
                                                       "is_lead": pk == trip.lead_id})
    for emp_id, due, n in (
        Task.objects.filter(status__in=[TaskStatus.TODO, TaskStatus.IN_PROGRESS], due_date__range=(date_from, date_to))
        .values_list("assignee", "due_date").annotate(n=Count("id"))
    ):
        if emp_id:
            cells[(emp_id, due)]["tasks"] += n
    for emp_id, received, n in (
        WorkOrder.objects.filter(kind=WorkOrderKind.LAB, received_at__date__range=(date_from, date_to))
        .values_list("assigned_employee", "received_at__date").annotate(n=Count("id"))
    ):
        cells[(emp_id, received)]["lab"] += n
    return {
        "date_from": date_from, "date_to": date_to, "days": days,
        "employees": [
            {
                "id": e.pk, "full_name": e.full_name, "short_name": e.short_name or short_name(e.full_name),
                "position": e.position,
                "cells": {d.isoformat(): cells[(e.pk, d)] for d in days if (e.pk, d) in cells},
            }
            for e in employees
        ],
    }
