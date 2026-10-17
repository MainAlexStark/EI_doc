"""Личный кабинет для ассистента: мой график, наряды, задачи, заявки с сайта.

Чтение — сразу. Любое изменение — как и всё остальное у ассистента — через
``PendingAction``: инструмент ``*_propose`` проверяет и раскрывает просьбу
человека в конкретный список изменений («17.10, 18.10, … 14:00–16:00»),
карточка показывает его, а выполняет ``EXECUTORS[kind]`` от имени нажавшего
«Подтвердить», заново проверяя права и состояние (между предложением и
нажатием могло пройти время).

Права — те же, что у экранов, местами строже:
* график — свой; чужой — владелец/руководитель/менеджер (как AvailabilityListCreateView);
* наряд — свой (исполнитель) или владелец/руководитель/менеджер; переназначить — только они;
  закрыть вручную нельзя — наряд закрывается сам, когда нормоконтроль принял все поверки;
* задачи — свои (исполнитель или постановщик) или владелец/руководитель/менеджер/метролог;
* заявки — владелец/руководитель/менеджер (диспетчер);
* наблюдатель ничего не меняет.

Импортирует tools.py, а tools.py берёт отсюда TOOLS лениво (cabinet_tools()) —
чтобы не было циклического импорта.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.assistant.actions import ActionError
from apps.assistant.models import PendingAction
from apps.assistant.tools import INT, STR, Tool, ToolContext, ToolFailure, ToolResult, _dump, _obj, _source
from apps.core.models import Employee, Role

MANAGE_ANY_ROLES = {Role.OWNER, Role.HEAD, Role.MANAGER}
TASK_MANAGE_ROLES = MANAGE_ANY_ROLES | {Role.METROLOGIST}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MAX_SLOTS = 120
MAX_SPAN_DAYS = 92
BOOL = {"type": "boolean"}
DATE = {"type": "string", "description": "YYYY-MM-DD"}
TIME = {"type": "string", "description": "HH:MM"}


# ===========================================================================
# Общее
# ===========================================================================
def _me(user) -> Employee | None:
    return getattr(user, "employee", None)


def _role(user) -> str:
    return getattr(user, "role", "")


def _is_manager(user) -> bool:
    return _role(user) in MANAGE_ANY_ROLES


def _require_writer(user) -> None:
    if _role(user) == Role.OBSERVER:
        raise ToolFailure("Наблюдатель ничего не меняет в EI_doc")


def _date(value, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ToolFailure(f"{field}: дата в формате YYYY-MM-DD") from exc


def _opt_date(value, field: str) -> dt.date | None:
    return _date(value, field) if value not in (None, "") else None


def _time(value, field: str) -> dt.time | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.isdigit():
        text = f"{int(text):02d}:00"
    try:
        return dt.time.fromisoformat(text)
    except ValueError as exc:
        raise ToolFailure(f"{field}: время в формате HH:MM") from exc


def _d(value: dt.date | None) -> str:
    return f"{value:%d.%m.%Y} ({WEEKDAYS[value.weekday()]})" if value else "—"


def _t(value: dt.time | None) -> str:
    return value.strftime("%H:%M") if value else ""


def _span(start: dt.time | None, end: dt.time | None) -> str:
    return f"{_t(start)}–{_t(end)}" if start and end else "весь день"


def _compact_dates(dates: list[dt.date]) -> str:
    """17.10–21.10, 24.10 — подряд идущие дни склеиваются в диапазон."""
    dates = sorted(set(dates))
    parts, i = [], 0
    while i < len(dates):
        j = i
        while j + 1 < len(dates) and dates[j + 1] - dates[j] == dt.timedelta(days=1):
            j += 1
        a, b = dates[i], dates[j]
        parts.append(f"{a:%d.%m}" if a == b else f"{a:%d.%m}–{b:%d.%m}")
        i = j + 1
    year = {d.year for d in dates}
    return ", ".join(parts) + (f" {dates[0].year}" if len(year) == 1 and dates else "")


def _employee_for(user, employee_id) -> Employee:
    """Сотрудник, чей график/задачи трогаем: свой по умолчанию, чужой — только руководству."""
    me = _me(user)
    if employee_id in (None, "", "me"):
        if me is None:
            raise ToolFailure("К учётной записи пользователя не привязан сотрудник — личного графика нет")
        return me
    employee = Employee.objects.filter(pk=employee_id, is_active=True).first()
    if employee is None:
        raise ToolFailure(f"Сотрудника с id {employee_id} нет (ei_reference what=employees)")
    if employee != me and not _is_manager(user):
        raise ToolFailure("Чужой график/наряды меняет только владелец, руководитель или менеджер")
    return employee


def _action(ctx: ToolContext, kind: str, title: str, payload: dict, view: list, note: str) -> ToolResult:
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind=kind, title=title[:250], payload={**payload, "view": view},
    )
    return ToolResult(
        f"Карточка «{title}» показана человеку с кнопкой «Подтвердить». {note} "
        "Ничего не изменено, пока не придёт событие о подтверждении.",
        {"action": action.pk},
    )


# ===========================================================================
# Кто я и что у меня
# ===========================================================================
def ei_me(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.hub.models import EmployeeAvailability
    from apps.tasks.models import Task, TaskStatus
    from apps.verification.models import WorkOrder, WorkOrderStatus

    user, me = ctx.user, _me(ctx.user)
    today = timezone.localdate()
    data = {
        "email": user.email, "role": _role(user), "role_display": user.get_role_display(),
        "can_manage_others": _is_manager(user), "today": _d(today), "employee": None,
    }
    if me:
        horizon = today + dt.timedelta(days=14)
        data["employee"] = {
            "id": me.pk, "full_name": me.full_name, "tab_number": me.tab_number, "position": me.position,
            "telegram_linked": bool(me.telegram_chat_id),
        }
        data["availability_next_14_days"] = [
            {"id": s.pk, "date": _d(s.date), "time": _span(s.start_time, s.end_time), "kind": s.kind,
             "priority": s.is_priority}
            for s in EmployeeAvailability.objects.filter(employee=me, date__gte=today, date__lte=horizon)
        ]
        data["open_work_orders"] = [
            {"id": w.pk, "date": _d(w.scheduled_date), "time": _t(w.scheduled_time), "status": w.status,
             "client": w.client.name, "address": w.site.address}
            for w in WorkOrder.objects.select_related("client", "site").filter(
                assigned_employee=me, status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.IN_PROGRESS],
            ).order_by("scheduled_date", "scheduled_time")[:30]
        ]
        data["open_tasks"] = [
            {"id": t.pk, "title": t.title, "status": t.status, "priority": t.priority, "due": _d(t.due_date),
             "overdue": bool(t.due_date and t.due_date < today)}
            for t in Task.objects.filter(assignee=me, status__in=[TaskStatus.TODO, TaskStatus.IN_PROGRESS])[:30]
        ]
    return ToolResult(_dump(data), {"sources": [_source("ei", "Личный кабинет EI_doc")]})


# ===========================================================================
# График (доступность)
# ===========================================================================
def _slot_row(s) -> dict:
    return {
        "id": s.pk, "date": s.date.isoformat(), "weekday": WEEKDAYS[s.date.weekday()],
        "start_time": _t(s.start_time) or None, "end_time": _t(s.end_time) or None, "kind": s.kind,
        "is_priority": s.is_priority, "note": s.note, "employee": s.employee.full_name,
    }


def ei_availability(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.hub.models import EmployeeAvailability

    employee = _employee_for(ctx.user, args.get("employee_id"))
    today = timezone.localdate()
    date_from = _opt_date(args.get("date_from"), "date_from") or today
    date_to = _opt_date(args.get("date_to"), "date_to") or date_from + dt.timedelta(days=30)
    qs = EmployeeAvailability.objects.select_related("employee").filter(
        employee=employee, date__gte=date_from, date__lte=date_to,
    )
    rows = [_slot_row(s) for s in qs[:300]]
    head = f"График {employee.full_name} с {_d(date_from)} по {_d(date_to)}: слотов {len(rows)}."
    return ToolResult(head + ("\n" + _dump(rows) if rows else " Пусто."),
                      {"sources": [_source("ei", f"График: {employee.full_name}")]})


def _expand(rule: dict, today: dt.date) -> list[dt.date]:
    if rule.get("dates"):
        dates = [_date(x, "dates") for x in rule["dates"]]
    else:
        if not rule.get("date_from") or not rule.get("date_to"):
            raise ToolFailure("Нужны dates[] или date_from + date_to (+ weekdays)")
        start, end = _date(rule["date_from"], "date_from"), _date(rule["date_to"], "date_to")
        if end < start:
            raise ToolFailure("date_to раньше date_from")
        if (end - start).days > MAX_SPAN_DAYS:
            raise ToolFailure(f"Слишком длинный период — не больше {MAX_SPAN_DAYS} дней за раз")
        days = {int(x) for x in (rule.get("weekdays") or range(1, 8))}
        if not days <= set(range(1, 8)):
            raise ToolFailure("weekdays — числа 1..7 (1 — понедельник, 7 — воскресенье)")
        dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        dates = [d for d in dates if d.isoweekday() in days]
    return sorted(set(dates))


def ei_availability_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.hub.models import AvailabilityKind, EmployeeAvailability

    _require_writer(ctx.user)
    employee = _employee_for(ctx.user, args.get("employee_id"))
    today = timezone.localdate()
    replace = bool(args.get("replace_existing"))

    create: list[dict] = []
    for rule in args.get("add") or []:
        start, end = _time(rule.get("start_time"), "start_time"), _time(rule.get("end_time"), "end_time")
        if bool(start) != bool(end):
            raise ToolFailure("Время указывается парой start_time + end_time (или ни того, ни другого — весь день)")
        if start and end and start >= end:
            raise ToolFailure("start_time должно быть раньше end_time")
        kind = rule.get("kind") or AvailabilityKind.DISTRICT
        if kind not in AvailabilityKind.values:
            raise ToolFailure("kind: district (выезд в своём районе) | trip (командировка)")
        dates = _expand(rule, today)
        past = [d for d in dates if d < today]
        if past:
            raise ToolFailure(f"Нельзя заводить график задним числом: {_compact_dates(past)}")
        for d in dates:
            create.append({
                "date": d.isoformat(), "start_time": _t(start) or None, "end_time": _t(end) or None,
                "kind": kind, "is_priority": bool(rule.get("is_priority")), "note": str(rule.get("note") or "")[:200],
            })

    existing = EmployeeAvailability.objects.filter(employee=employee)
    delete_q = Q(pk__in=[])
    remove = args.get("remove") or {}
    if remove.get("ids"):
        delete_q |= Q(pk__in=[int(x) for x in remove["ids"]])
    if remove.get("dates") or remove.get("date_from"):
        delete_q |= Q(date__in=_expand(remove, today))
    if replace and create:
        delete_q |= Q(date__in=[c["date"] for c in create])
    to_delete = list(existing.filter(delete_q).filter(date__gte=today))
    if remove.get("ids") and len(to_delete) < len(set(remove["ids"])):
        raise ToolFailure("Часть ids не найдена в будущем графике этого сотрудника — возьми id из ei_availability")
    delete_ids = {s.pk for s in to_delete}

    # повторы и пересечения с тем, что остаётся
    keep = [s for s in existing.filter(date__in=[c["date"] for c in create]) if s.pk not in delete_ids]
    duplicates, overlaps, final = [], [], []
    for c in create:
        same_day = [s for s in keep if s.date.isoformat() == c["date"]]
        st, en = _time(c["start_time"], "t"), _time(c["end_time"], "t")
        if any(s.start_time == st and s.end_time == en and s.kind == c["kind"] for s in same_day):
            duplicates.append(dt.date.fromisoformat(c["date"]))
            continue
        if same_day:
            overlaps.append(dt.date.fromisoformat(c["date"]))
        final.append(c)
    if len(final) > MAX_SLOTS:
        raise ToolFailure(f"Слишком много слотов за раз ({len(final)}) — не больше {MAX_SLOTS}")
    if not final and not to_delete:
        msg = "Менять нечего."
        if duplicates:
            msg += f" Такие слоты уже есть: {_compact_dates(duplicates)}."
        return ToolResult(msg)

    view: list[list[str]] = [["Сотрудник", employee.full_name]]
    groups: dict[tuple, list[dt.date]] = {}
    for c in final:
        key = (c["start_time"], c["end_time"], c["kind"], c["is_priority"], c["note"])
        groups.setdefault(key, []).append(dt.date.fromisoformat(c["date"]))
    for (st, en, kind, prio, note), dates in groups.items():
        label = f"Добавить ({len(dates)} дн.)"
        value = f"{_compact_dates(dates)} · {_span(_time(st, 't'), _time(en, 't'))} · " + (
            "командировка" if kind == AvailabilityKind.TRIP else "выезд в районе"
        ) + (" · приоритетное" if prio else "") + (f" · {note}" if note else "")
        view.append([label, value])
    if to_delete:
        view.append([f"Удалить ({len(to_delete)})", "; ".join(
            f"{s.date:%d.%m} {_span(s.start_time, s.end_time)}" for s in sorted(to_delete, key=lambda s: s.date)
        )])
    if duplicates:
        view.append(["Уже есть, пропущено", _compact_dates(duplicates)])
    if overlaps:
        view.append(["⚠ В эти дни уже есть другие слоты", _compact_dates(overlaps)])

    parts = []
    if final:
        parts.append(f"добавить {len(final)}")
    if to_delete:
        parts.append(f"удалить {len(to_delete)}")
    title = f"График {employee.short_name or employee.full_name}: " + ", ".join(parts)
    note = f"Добавить: {_compact_dates([dt.date.fromisoformat(c['date']) for c in final])}." if final else ""
    if duplicates:
        note += f" Уже были (пропущены): {_compact_dates(duplicates)}."
    if overlaps:
        note += f" В дни {_compact_dates(overlaps)} уже есть другие слоты — они останутся (replace_existing=true заменит)."
    return _action(ctx, "availability_change", title,
                   {"employee_id": employee.pk, "create": final, "delete_ids": sorted(delete_ids)}, view, note)


def _availability_change(action: PendingAction, user) -> dict:
    from apps.hub.models import EmployeeAvailability

    if _role(user) == Role.OBSERVER:
        raise PermissionDenied("Наблюдатель ничего не меняет в EI_doc")
    employee = Employee.objects.filter(pk=action.payload["employee_id"]).first()
    if employee is None:
        raise ActionError("Сотрудник не найден")
    if employee != _me(user) and not _is_manager(user):
        raise PermissionDenied("Чужой график меняет только владелец, руководитель или менеджер")
    with transaction.atomic():
        deleted, _ = EmployeeAvailability.objects.filter(
            employee=employee, pk__in=action.payload.get("delete_ids") or [],
        ).delete()
        created = 0
        for c in action.payload.get("create") or []:
            fields = dict(
                employee=employee, date=dt.date.fromisoformat(c["date"]), kind=c["kind"],
                start_time=_time(c["start_time"], "t"), end_time=_time(c["end_time"], "t"),
            )
            if EmployeeAvailability.objects.filter(**fields).exists():
                continue
            EmployeeAvailability.objects.create(**fields, is_priority=c["is_priority"], note=c.get("note", ""))
            created += 1
    return {"created": created, "deleted": deleted}


# ===========================================================================
# Наряды
# ===========================================================================
def _wo_row(w, *, details: bool = False) -> dict:
    row = {
        "id": w.pk, "status": w.status, "status_display": w.get_status_display(),
        "date": w.scheduled_date.isoformat() if w.scheduled_date else None, "time": _t(w.scheduled_time) or None,
        "client": w.client.name, "address": w.site.address, "employee": w.assigned_employee.full_name,
        "employee_id": w.assigned_employee_id, "note": w.note, "request_id": w.request_id,
        "verifications": len(w.verifications.all()),
        "accepted": sum(1 for v in w.verifications.all() if v.status == "accepted"),
    }
    if details:
        row["verifications_list"] = [
            {"id": v.pk, "si": v.instrument.si_type.name, "serial_number": v.instrument.serial_number,
             "status": v.status, "suitable": v.suitable}
            for v in w.verifications.select_related("instrument__si_type")
        ]
        if w.request_id:
            r = w.request
            row["request"] = {"contact": r.contact_name, "phone": r.contact_phone, "comment": r.comment,
                              "items": [f"{i.family.name} × {i.quantity}" for i in r.items.select_related("family")]}
    return row


def ei_work_orders(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.verification.models import WorkOrder

    qs = WorkOrder.objects.select_related("client", "site", "assigned_employee", "request").prefetch_related(
        "verifications"
    ).order_by("scheduled_date", "scheduled_time", "pk")
    if args.get("id"):
        w = qs.filter(pk=args["id"]).first()
        if w is None:
            raise ToolFailure(f"Наряда №{args['id']} нет")
        return ToolResult(_dump(_wo_row(w, details=True)), {"sources": [_source("ei", f"Наряд №{w.pk}")]})
    if args.get("employee_id"):
        qs = qs.filter(assigned_employee_id=args["employee_id"])
    elif args.get("mine", True):
        me = _me(ctx.user)
        qs = qs.filter(assigned_employee=me) if me else qs.none()
    statuses = args.get("status") or (["planned", "in_progress"] if not args.get("include_closed") else [])
    if isinstance(statuses, str):
        statuses = [statuses]
    if statuses:
        qs = qs.filter(status__in=statuses)
    if args.get("date_from"):
        qs = qs.filter(scheduled_date__gte=_date(args["date_from"], "date_from"))
    if args.get("date_to"):
        qs = qs.filter(scheduled_date__lte=_date(args["date_to"], "date_to"))
    for word in str(args.get("query") or "").split():
        qs = qs.filter(Q(client__name__icontains=word) | Q(site__address__icontains=word) | Q(note__icontains=word))
    rows = [_wo_row(w) for w in qs[: min(int(args.get("limit") or 50), 200)]]
    return ToolResult(_dump(rows) if rows else "Нарядов не найдено.", {"sources": [_source("ei", "Наряды EI_doc")]})


def ei_work_order_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.verification.models import WorkOrder, WorkOrderStatus

    _require_writer(ctx.user)
    w = WorkOrder.objects.select_related("site", "assigned_employee").filter(pk=args.get("work_order_id") or 0).first()
    if w is None:
        raise ToolFailure("Наряд не найден — возьми id из ei_work_orders")
    if w.assigned_employee != _me(ctx.user) and not _is_manager(ctx.user):
        raise ToolFailure("Чужой наряд меняет только владелец, руководитель или менеджер")
    changes, view = {}, [["Наряд", f"№{w.pk} — {w.site.address}, {w.assigned_employee.full_name}"]]
    status = args.get("status")
    if status:
        if status == WorkOrderStatus.DONE or status == WorkOrderStatus.IN_PROGRESS:
            raise ToolFailure("«Закрыт»/«В работе» вручную не ставятся — статус считается сам по поверкам "
                              "(закрыт, когда нормоконтроль принял все). Вручную: cancelled или planned (вернуть из отмены)")
        if status not in (WorkOrderStatus.CANCELLED, WorkOrderStatus.PLANNED):
            raise ToolFailure("status: cancelled | planned")
        if status == WorkOrderStatus.PLANNED and w.status != WorkOrderStatus.CANCELLED:
            raise ToolFailure("planned — только чтобы вернуть отменённый наряд")
        if status == w.status:
            raise ToolFailure(f"Наряд уже в статусе «{w.get_status_display()}»")
        changes["status"] = status
        view.append(["Статус", f"{w.get_status_display()} → {WorkOrderStatus(status).label}"])
    if w.is_closed and status != WorkOrderStatus.PLANNED and any(k in args for k in ("scheduled_date", "scheduled_time")):
        raise ToolFailure("Закрытый/отменённый наряд не переносят")
    if "scheduled_date" in args:
        new = _opt_date(args["scheduled_date"], "scheduled_date")
        if new and new < timezone.localdate():
            raise ToolFailure("Нельзя перенести наряд в прошлое")
        changes["scheduled_date"] = new.isoformat() if new else None
        view.append(["Дата", f"{_d(w.scheduled_date)} → {_d(new)}"])
    if "scheduled_time" in args:
        new_t = _time(args["scheduled_time"], "scheduled_time")
        changes["scheduled_time"] = _t(new_t) or None
        view.append(["Время", f"{_t(w.scheduled_time) or '—'} → {_t(new_t) or '—'}"])
    if args.get("note") is not None and "note" in args:
        changes["note"] = str(args["note"])
        view.append(["Примечание", changes["note"] or "(очистить)"])
    if args.get("assigned_employee_id"):
        if not _is_manager(ctx.user):
            raise ToolFailure("Переназначить наряд может только владелец, руководитель или менеджер")
        emp = Employee.objects.filter(pk=args["assigned_employee_id"], is_active=True).first()
        if emp is None:
            raise ToolFailure("Сотрудник не найден (ei_reference what=employees)")
        changes["assigned_employee_id"] = emp.pk
        view.append(["Исполнитель", f"{w.assigned_employee.full_name} → {emp.full_name}"])
    if not changes:
        raise ToolFailure("Нечего менять: status / scheduled_date / scheduled_time / note / assigned_employee_id")
    if args.get("reason"):
        view.append(["Причина", str(args["reason"])])
    title = f"Наряд №{w.pk}: " + ", ".join({"status": "статус", "scheduled_date": "дата", "scheduled_time": "время",
                                             "note": "примечание", "assigned_employee_id": "исполнитель"}[k]
                                            for k in changes)
    return _action(ctx, "work_order_change", title, {"work_order_id": w.pk, "changes": changes}, view, "")


def _work_order_change(action: PendingAction, user) -> dict:
    from apps.hub.service import notify_work_order_assigned
    from apps.verification.models import WorkOrder, WorkOrderStatus

    if _role(user) == Role.OBSERVER:
        raise PermissionDenied("Наблюдатель ничего не меняет в EI_doc")
    w = WorkOrder.objects.filter(pk=action.payload["work_order_id"]).first()
    if w is None:
        raise ActionError("Наряд уже удалён")
    if w.assigned_employee != _me(user) and not _is_manager(user):
        raise PermissionDenied("Чужой наряд меняет только владелец, руководитель или менеджер")
    ch = action.payload["changes"]
    if "assigned_employee_id" in ch and not _is_manager(user):
        raise PermissionDenied("Переназначить наряд может только владелец, руководитель или менеджер")
    reassigned = "assigned_employee_id" in ch and ch["assigned_employee_id"] != w.assigned_employee_id
    if "scheduled_date" in ch:
        w.scheduled_date = dt.date.fromisoformat(ch["scheduled_date"]) if ch["scheduled_date"] else None
    if "scheduled_time" in ch:
        w.scheduled_time = _time(ch["scheduled_time"], "t")
    if "note" in ch:
        w.note = ch["note"]
    if "assigned_employee_id" in ch:
        w.assigned_employee_id = ch["assigned_employee_id"]
    if "status" in ch:
        w.status = ch["status"]
        w.closed_at = None
    w.save()
    if "status" in ch:
        w.sync_request_status()
        if w.status == WorkOrderStatus.PLANNED:
            w.refresh_status()  # вернули из отмены — пусть статус снова посчитают поверки
    if reassigned:
        notify_work_order_assigned(w)
    w.refresh_from_db()
    return {"id": w.pk, "status": w.get_status_display()}


# ===========================================================================
# Задачи
# ===========================================================================
def _task_row(t, *, details: bool = False) -> dict:
    row = {
        "id": t.pk, "title": t.title, "status": t.status, "priority": t.priority,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "assignee": t.assignee.full_name if t.assignee else "", "assignee_id": t.assignee_id,
        "created_by": t.created_by.full_name if t.created_by else "", "parent": t.parent_id,
        "estimated_hours": float(t.estimated_hours) if t.estimated_hours is not None else None,
        "progress": t.progress, "recurrence": t.recurrence,
        "linked": str(t.linked_object) if t.object_id else "",
    }
    if details:
        row["description"] = t.description
        row["children"] = [{"id": c.pk, "title": c.title, "status": c.status} for c in t.children.all()]
    return row


def ei_tasks(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.tasks.models import Task, TaskStatus

    qs = Task.objects.select_related("assignee", "created_by", "content_type").prefetch_related("children")
    if args.get("id"):
        t = qs.filter(pk=args["id"]).first()
        if t is None:
            raise ToolFailure(f"Задачи №{args['id']} нет")
        return ToolResult(_dump(_task_row(t, details=True)), {"sources": [_source("ei", f"Задача №{t.pk}")]})
    if args.get("assignee_id"):
        qs = qs.filter(assignee_id=args["assignee_id"])
    elif args.get("mine", True):
        me = _me(ctx.user)
        qs = qs.filter(Q(assignee=me) | Q(assignee__isnull=True, created_by=me)) if me else qs.none()
    statuses = args.get("status") or ([] if args.get("include_done") else [TaskStatus.TODO, TaskStatus.IN_PROGRESS])
    if isinstance(statuses, str):
        statuses = [statuses]
    if statuses:
        qs = qs.filter(status__in=statuses)
    if args.get("due_from"):
        qs = qs.filter(due_date__gte=_date(args["due_from"], "due_from"))
    if args.get("due_to"):
        qs = qs.filter(due_date__lte=_date(args["due_to"], "due_to"))
    for word in str(args.get("query") or "").split():
        qs = qs.filter(Q(title__icontains=word) | Q(description__icontains=word))
    rows = [_task_row(t) for t in qs[: min(int(args.get("limit") or 50), 200)]]
    return ToolResult(_dump(rows) if rows else "Задач не найдено.", {"sources": [_source("ei", "Задачи EI_doc")]})


TASK_FIELDS = ("title", "description", "status", "priority", "due_date", "estimated_hours", "assignee_id",
               "parent_id", "work_order_id", "recurrence")


def _can_touch_task(user, task) -> bool:
    me = _me(user)
    return _role(user) in TASK_MANAGE_ROLES or (me is not None and me.pk in (task.assignee_id, task.created_by_id))


def _clean_task(item: dict, user, task=None) -> tuple[dict, list[str]]:
    from apps.tasks.models import TaskPriority, TaskStatus
    from apps.verification.models import WorkOrder

    data, view = {}, []
    if "title" in item:
        title = str(item["title"]).strip()
        if not title:
            raise ToolFailure("title пустой")
        data["title"] = title[:250]
        view.append(f"«{data['title']}»")
    if "description" in item:
        data["description"] = str(item["description"] or "")
    if item.get("status"):
        if item["status"] not in TaskStatus.values:
            raise ToolFailure("status: todo | in_progress | done | cancelled")
        data["status"] = item["status"]
        view.append(f"статус: {TaskStatus(item['status']).label}")
    if item.get("priority"):
        if item["priority"] not in TaskPriority.values:
            raise ToolFailure("priority: low | normal | high | urgent")
        data["priority"] = item["priority"]
        view.append(f"приоритет: {TaskPriority(item['priority']).label}")
    if "due_date" in item:
        due = _opt_date(item["due_date"], "due_date")
        data["due_date"] = due.isoformat() if due else None
        view.append(f"срок: {_d(due)}")
    if "estimated_hours" in item:
        data["estimated_hours"] = item["estimated_hours"]
        view.append(f"оценка: {item['estimated_hours']} ч")
    if "assignee_id" in item:
        if item["assignee_id"] in (None, ""):
            data["assignee_id"] = None
            view.append("исполнитель: никто")
        else:
            emp = _me(user) if item["assignee_id"] == "me" else Employee.objects.filter(
                pk=item["assignee_id"], is_active=True).first()
            if emp is None:
                raise ToolFailure("Исполнитель не найден (ei_reference what=employees)")
            data["assignee_id"] = emp.pk
            view.append(f"исполнитель: {emp.full_name}")
    if item.get("parent_id"):
        from apps.tasks.models import Task
        parent = Task.objects.filter(pk=item["parent_id"]).first()
        if parent is None or (task and parent.pk == task.pk):
            raise ToolFailure("parent_id: нет такой задачи")
        data["parent_id"] = parent.pk
        view.append(f"подзадача к №{parent.pk} «{parent.title}»")
    if item.get("work_order_id"):
        wo = WorkOrder.objects.filter(pk=item["work_order_id"]).first()
        if wo is None:
            raise ToolFailure("work_order_id: нет такого наряда")
        data["work_order_id"] = wo.pk
        view.append(f"к наряду №{wo.pk}")
    if "recurrence" in item:
        rec = str(item["recurrence"] or "").strip()
        if rec:
            from croniter import croniter
            if not croniter.is_valid(rec):
                raise ToolFailure("recurrence — cron из 5 полей, например «0 6 1 * *» (1-го числа каждого месяца)")
        data["recurrence"] = rec
        view.append(f"повтор: {rec or 'нет'}")
    return data, view


def ei_task_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.tasks.models import Task

    _require_writer(ctx.user)
    items = args.get("items") or []
    if not items:
        raise ToolFailure("items: [{op: create|update, ...}]")
    if len(items) > 30:
        raise ToolFailure("Не больше 30 задач за одну карточку")
    ops, view = [], []
    for n, item in enumerate(items, 1):
        op = item.get("op") or ("update" if item.get("task_id") else "create")
        fields = {k: v for k, v in item.items() if k in TASK_FIELDS}
        if op == "create":
            if not fields.get("title"):
                raise ToolFailure(f"items[{n}]: у новой задачи нужен title")
            fields.setdefault("assignee_id", "me" if _me(ctx.user) else None)
            data, lines = _clean_task(fields, ctx.user)
            ops.append({"op": "create", "data": data})
            view.append([f"{n}. Новая задача", ", ".join(lines)])
        elif op == "update":
            task = Task.objects.filter(pk=item.get("task_id") or 0).first()
            if task is None:
                raise ToolFailure(f"items[{n}]: задачи №{item.get('task_id')} нет — возьми id из ei_tasks")
            if not _can_touch_task(ctx.user, task):
                raise ToolFailure(f"items[{n}]: чужую задачу №{task.pk} меняет исполнитель, постановщик или руководство")
            data, lines = _clean_task(fields, ctx.user, task)
            if not data:
                raise ToolFailure(f"items[{n}]: нечего менять у задачи №{task.pk}")
            ops.append({"op": "update", "task_id": task.pk, "data": data})
            view.append([f"{n}. Задача №{task.pk} «{task.title}»", ", ".join(lines)])
        else:
            raise ToolFailure("op: create | update (удалить — status=cancelled)")
    creates = sum(1 for o in ops if o["op"] == "create")
    title = "Задачи: " + ", ".join(p for p in (
        f"создать {creates}" if creates else "", f"изменить {len(ops) - creates}" if len(ops) - creates else ""
    ) if p)
    return _action(ctx, "tasks_change", title, {"ops": ops}, view, "")


def _tasks_change(action: PendingAction, user) -> dict:
    from django.contrib.contenttypes.models import ContentType

    from apps.tasks.models import Task
    from apps.verification.models import WorkOrder

    if _role(user) == Role.OBSERVER:
        raise PermissionDenied("Наблюдатель ничего не меняет в EI_doc")
    created, updated = [], []
    with transaction.atomic():
        for op in action.payload["ops"]:
            data = dict(op["data"])
            wo = data.pop("work_order_id", None)
            if data.get("due_date"):
                data["due_date"] = dt.date.fromisoformat(data["due_date"])
            if wo:
                data["content_type"] = ContentType.objects.get_for_model(WorkOrder)
                data["object_id"] = wo
            if op["op"] == "create":
                task = Task(created_by=_me(user), **data)
                task.full_clean(exclude=["content_type", "object_id"])
                task.save()
                created.append(task.pk)
            else:
                task = Task.objects.select_for_update().filter(pk=op["task_id"]).first()
                if task is None:
                    raise ActionError(f"Задачи №{op['task_id']} уже нет")
                if not _can_touch_task(user, task):
                    raise PermissionDenied(f"Чужую задачу №{task.pk} меняет исполнитель, постановщик или руководство")
                for key, value in data.items():
                    setattr(task, key, value)
                task.full_clean(exclude=["content_type", "object_id"])
                task.save()
                updated.append(task.pk)
    return {"created": created, "updated": updated}


# ===========================================================================
# Заявки с сайта (диспетчер)
# ===========================================================================
def _require_dispatcher(user) -> None:
    if not _is_manager(user):
        raise ToolFailure("Заявки с сайта ведёт владелец, руководитель или менеджер")


def ei_requests(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.hub.models import Request

    _require_dispatcher(ctx.user)
    qs = Request.objects.select_related("district", "suggested_employee", "assigned_employee").prefetch_related(
        "items__family").order_by("-created_at")
    if args.get("id"):
        qs = qs.filter(pk=args["id"])
    else:
        statuses = args.get("status") or ["new", "routed"]
        qs = qs.filter(status__in=[statuses] if isinstance(statuses, str) else statuses)
    rows = [
        {"id": r.pk, "status": r.status, "created": timezone.localtime(r.created_at).strftime("%d.%m.%Y %H:%M"),
         "contact": r.contact_name, "phone": r.contact_phone, "email": r.contact_email, "address": r.address,
         "district": r.district.name if r.district else "", "desired_date": r.desired_date, "desired_time": r.desired_time,
         "items": [f"{i.family.name} × {i.quantity}" for i in r.items.all()], "comment": r.comment,
         "estimated_price": r.estimated_price,
         "suggested_employee": r.suggested_employee.full_name if r.suggested_employee else "",
         "suggested_employee_id": r.suggested_employee_id}
        for r in qs[: min(int(args.get("limit") or 30), 100)]
    ]
    return ToolResult(_dump(rows) if rows else "Заявок не найдено.", {"sources": [_source("ei", "Заявки с сайта")]})


def ei_request_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.hub.models import Request, RequestStatus

    _require_writer(ctx.user)
    _require_dispatcher(ctx.user)
    r = Request.objects.filter(pk=args.get("request_id") or 0).first()
    if r is None:
        raise ToolFailure("Заявка не найдена — возьми id из ei_requests")
    op = args.get("op")
    view = [["Заявка", f"№{r.pk} — {r.contact_name}, {r.address}"]]
    if op == "confirm":
        if r.status not in (RequestStatus.NEW, RequestStatus.ROUTED):
            raise ToolFailure(f"Заявка в статусе «{r.get_status_display()}» — подтверждать нечего")
        emp = Employee.objects.filter(pk=args.get("employee_id") or 0, is_active=True).first()
        if emp is None:
            raise ToolFailure("employee_id — исполнитель (suggested_employee_id из заявки или кого назвал человек)")
        date = _opt_date(args.get("scheduled_date"), "scheduled_date") or r.desired_date
        time = _time(args.get("scheduled_time"), "scheduled_time") or r.desired_time
        payload = {"request_id": r.pk, "op": "confirm", "employee_id": emp.pk,
                   "scheduled_date": date.isoformat() if date else None, "scheduled_time": _t(time) or None,
                   "note": str(args.get("note") or "")}
        view += [["Исполнитель", emp.full_name], ["Дата", _d(date)], ["Время", _t(time) or "—"]]
        if payload["note"]:
            view.append(["Примечание", payload["note"]])
        view.append(["Будет создан", "наряд + новый клиент и объект из данных заявки"])
        title = f"Подтвердить заявку №{r.pk} → наряд на {emp.short_name or emp.full_name}"
    elif op in ("reject", "spam"):
        if r.status in (RequestStatus.CONFIRMED, RequestStatus.DONE):
            raise ToolFailure("По заявке уже есть наряд — отменяйте наряд (ei_work_order_propose status=cancelled)")
        payload = {"request_id": r.pk, "op": op, "reason": str(args.get("reason") or "")[:250]}
        view.append(["Причина", payload["reason"] or "—"])
        title = f"{'Пометить спамом' if op == 'spam' else 'Отклонить'} заявку №{r.pk}"
    else:
        raise ToolFailure("op: confirm | reject | spam")
    return _action(ctx, "request_decide", title, payload, view, "")


def _request_decide(action: PendingAction, user) -> dict:
    from apps.hub import service
    from apps.hub.models import Request

    if not _is_manager(user):
        raise PermissionDenied("Заявки с сайта ведёт владелец, руководитель или менеджер")
    p = action.payload
    r = Request.objects.filter(pk=p["request_id"]).first()
    if r is None:
        raise ActionError("Заявка уже удалена")
    try:
        if p["op"] == "confirm":
            emp = Employee.objects.get(pk=p["employee_id"])
            wo = service.confirm_request(
                r, employee=emp, note=p.get("note", ""),
                scheduled_date=dt.date.fromisoformat(p["scheduled_date"]) if p.get("scheduled_date") else None,
                scheduled_time=_time(p.get("scheduled_time"), "t"),
            )
            return {"work_order_id": wo.pk}
        service.reject_request(r, reason=p.get("reason", ""), spam=p["op"] == "spam")
        return {"status": r.status}
    except service.RequestActionError as exc:
        raise ActionError(str(exc)) from exc


# ===========================================================================
# Регистрация
# ===========================================================================
EXECUTORS = {
    "availability_change": _availability_change,
    "work_order_change": _work_order_change,
    "tasks_change": _tasks_change,
    "request_decide": _request_decide,
}

DONE_TEXT = {
    "availability_change": lambda r: f"График обновлён: добавлено {r['created']}, удалено {r['deleted']}.",
    "work_order_change": lambda r: f"Наряд №{r['id']} обновлён, статус — {r['status']}.",
    "tasks_change": lambda r: "Задачи: " + ", ".join(p for p in (
        f"созданы №{', '.join(map(str, r['created']))}" if r["created"] else "",
        f"изменены №{', '.join(map(str, r['updated']))}" if r["updated"] else "",
    ) if p) + ".",
    "request_decide": lambda r: (
        f"Заявка подтверждена, создан наряд №{r['work_order_id']}." if r.get("work_order_id")
        else f"Заявка закрыта, статус {r['status']}."
    ),
}

RANGE = {"dates": {"type": "array", "items": DATE}, "date_from": DATE, "date_to": DATE,
         "weekdays": {"type": "array", "items": INT, "description": "1=пн … 7=вс; пусто — все дни"}}

TOOLS: list[Tool] = [
    Tool(
        "ei_me", "Открываю личный кабинет",
        "Кто пользователь (сотрудник, роль, права) и что у него сейчас: график на 14 дней, открытые наряды, "
        "открытые задачи. Начинай с него любую просьбу «мой/мне/у меня».",
        _obj({}), ei_me,
    ),
    Tool(
        "ei_availability", "Смотрю график",
        "График доступности (слоты выездов) сотрудника за период: свой по умолчанию, чужой — employee_id "
        "(только владельцу/руководителю/менеджеру). По умолчанию — 30 дней от сегодня.",
        _obj({"employee_id": INT, "date_from": DATE, "date_to": DATE}), ei_availability,
    ),
    Tool(
        "ei_availability_propose", "Готовлю изменение графика",
        "Изменить график доступности (смены/слоты выездов) — карточка с кнопкой. add — список правил: "
        "{dates[] | date_from+date_to (+weekdays 1..7), start_time, end_time (HH:MM, оба или ни одного — весь день), "
        "kind: district (выезд в своём районе, по умолчанию) | trip (командировка), is_priority, note}. "
        "remove — {ids[] | dates[] | date_from+date_to (+weekdays)}: удалить слоты. replace_existing=true — сначала "
        "убрать прежние слоты в те же дни, что добавляются. Даты раскрываются на сервере, повторы пропускаются. "
        "employee_id — чужой график (только руководство).",
        _obj({
            "employee_id": INT,
            "add": {"type": "array", "items": {"type": "object", "properties": {
                **RANGE, "start_time": TIME, "end_time": TIME, "kind": {"type": "string", "enum": ["district", "trip"]},
                "is_priority": BOOL, "note": STR}}},
            "remove": {"type": "object", "properties": {"ids": {"type": "array", "items": INT}, **RANGE}},
            "replace_existing": BOOL,
        }), ei_availability_propose,
    ),
    Tool(
        "ei_work_orders", "Смотрю наряды",
        "Наряды: по умолчанию мои открытые (planned, in_progress). id — один наряд подробно (поверки внутри, данные "
        "заявки). Фильтры: mine=false — все, employee_id, status[], include_closed, date_from/date_to (по плановой "
        "дате), query (клиент, адрес, примечание).",
        _obj({"id": INT, "mine": BOOL, "employee_id": INT, "status": {"type": "array", "items": STR},
              "include_closed": BOOL, "date_from": DATE, "date_to": DATE, "query": STR, "limit": INT}),
        ei_work_orders,
    ),
    Tool(
        "ei_work_order_propose", "Готовлю изменение наряда",
        "Изменить наряд — карточка с кнопкой: перенести (scheduled_date, scheduled_time), отменить "
        "(status=cancelled, reason), вернуть из отмены (status=planned), примечание (note), переназначить "
        "(assigned_employee_id — только руководство). Закрыть вручную нельзя: наряд закрывается сам, когда "
        "нормоконтроль принял все поверки.",
        _obj({"work_order_id": INT, "status": {"type": "string", "enum": ["cancelled", "planned"]},
              "scheduled_date": DATE, "scheduled_time": TIME, "note": STR, "assigned_employee_id": INT,
              "reason": STR}, ["work_order_id"]),
        ei_work_order_propose,
    ),
    Tool(
        "ei_tasks", "Смотрю задачи",
        "Задачи: по умолчанию мои открытые (todo, in_progress). id — одна задача с описанием и подзадачами. "
        "Фильтры: mine=false — все, assignee_id, status[], include_done, due_from/due_to, query.",
        _obj({"id": INT, "mine": BOOL, "assignee_id": INT, "status": {"type": "array", "items": STR},
              "include_done": BOOL, "due_from": DATE, "due_to": DATE, "query": STR, "limit": INT}),
        ei_tasks,
    ),
    Tool(
        "ei_task_propose", "Готовлю задачи",
        "Создать или изменить задачи (до 30 за раз) — одна карточка с кнопкой. items[]: {op: create|update, task_id "
        "(для update), title, description, status: todo|in_progress|done|cancelled, priority: low|normal|high|urgent, "
        "due_date, estimated_hours, assignee_id (id сотрудника или \"me\"; у новой задачи по умолчанию — сам "
        "пользователь), parent_id (подзадача), work_order_id (привязать к наряду), recurrence (cron, «0 6 1 * *»)}. "
        "Удалять нельзя — status=cancelled.",
        _obj({"items": {"type": "array", "items": {"type": "object"}}}, ["items"]),
        ei_task_propose,
    ),
    Tool(
        "ei_requests", "Смотрю заявки с сайта",
        "Заявки с сайта (только владелец/руководитель/менеджер): по умолчанию новые и с подобранным исполнителем. "
        "id — одна заявка; status[] — new, routed, confirmed, done, cancelled, rejected, spam.",
        _obj({"id": INT, "status": {"type": "array", "items": STR}, "limit": INT}), ei_requests,
    ),
    Tool(
        "ei_request_propose", "Готовлю решение по заявке",
        "Решение по заявке — карточка с кнопкой (только владелец/руководитель/менеджер). op=confirm — создать наряд: "
        "employee_id (исполнитель), scheduled_date/scheduled_time (по умолчанию — желаемые из заявки), note; "
        "клиент и объект заводятся из данных заявки. op=reject | spam — reason.",
        _obj({"request_id": INT, "op": {"type": "string", "enum": ["confirm", "reject", "spam"]}, "employee_id": INT,
              "scheduled_date": DATE, "scheduled_time": TIME, "note": STR, "reason": STR}, ["request_id", "op"]),
        ei_request_propose,
    ),
]
