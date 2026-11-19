"""Администрирование для ассистента: вся фирма, назначение работы, сотрудники.

Те же операции, что у экранов руководства («Панель», карта «Выездов», «Приёмки»,
командный «График», «Сотрудники»), — через ``apps.logistics.dispatch`` и
``apps.core.staff``, поэтому правила одни и те же. Чтение — сразу, изменение —
карточкой ``PendingAction`` с кнопкой «Подтвердить», исполнитель заново
проверяет права нажавшего.

Права:
* видеть всю фирму и назначать — владелец, руководитель, метролог, менеджер;
* заводить/менять сотрудников, роли, пароли — владелец и руководитель.

Временный пароль после подтверждения видит только человек в карточке
(``action.result``) — в текст события для модели он не попадает.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.assistant.actions import ActionError
from apps.assistant.cabinet import BOOL, DATE, TIME, _action, _d, _opt_date, _time
from apps.assistant.models import PendingAction
from apps.assistant.tools import INT, STR, Tool, ToolContext, ToolFailure, ToolResult, _dump, _obj, _source
from apps.core import staff
from apps.core.models import Employee
from apps.logistics import dispatch

TYPE_LABEL = {"stop": "адрес", "order": "наряд/приёмка", "request": "заявка"}


def _require_control(user) -> None:
    if not dispatch.can_control(user):
        raise ToolFailure("Вся фирма и назначение работы — для владельца, руководителя, метролога и менеджера")


def _require_admin(user) -> None:
    if not staff.can_admin(user):
        raise ToolFailure("Сотрудников, роли и пароли меняют владелец и руководитель")


def _employee(employee_id) -> Employee:
    employee = Employee.objects.filter(pk=employee_id, is_active=True).first() if employee_id else None
    if employee is None:
        raise ToolFailure(f"Сотрудника с id {employee_id} нет — посмотри ei_team или ei_staff")
    return employee


# ===========================================================================
# Чтение
# ===========================================================================
def ei_team(ctx: ToolContext, args: dict) -> ToolResult:
    _require_control(ctx.user)
    day = _opt_date(args.get("date"), "date") or timezone.localdate()
    data = dispatch.overview(day)
    people = []
    for c in data["employees"]:
        people.append({
            "id": c["id"], "name": c["full_name"], "state": {
                "trip": "в командировке", "city": "выезды в городе", "free": "свободен (есть слоты)", "off": "нет слотов",
            }[c["state"]],
            "stops": c["stops"], "trips": [t["title"] for t in c["trips"]],
            "slots": [f"{s['start'] or ''}–{s['end'] or ''}".strip("–") or "весь день" for s in c["slots"]],
            "lab_open": c["lab_open"], "drafts": c["drafts"], "on_review": c["ready"], "returned": c["rejected"],
            "tasks_open": c["tasks_open"], "tasks_overdue": c["tasks_overdue"], "week_stops": c["week_stops"],
        })
    out = {"date": _d(day), "totals": data["totals"], "attention": data["attention"][:25], "employees": people}
    return ToolResult(_dump(out), {"sources": [_source("ei", f"Панель управления на {day:%d.%m.%Y}")]})


def ei_dispatch_board(ctx: ToolContext, args: dict) -> ToolResult:
    _require_control(ctx.user)
    start = _opt_date(args.get("date_from"), "date_from") or timezone.localdate()
    end = _opt_date(args.get("date_to"), "date_to") or start
    data = dispatch.map_points(start, end)
    by_emp: dict[str, list] = {}
    for p in [*data["points"], *data["unlocated"]]:
        by_emp.setdefault(f"{p['employee']} (id {p['employee_id']})", []).append({
            "stop_id": p["id"], "date": p["date"], "window": [p["window_start"], p["window_end"]],
            "address": p["address"], "status": p["status_display"], "trip": p["trip_title"],
            "trip_kind": p["trip_kind"], "no_coords": p["latitude"] is None,
        })
    queue = [{"type": q["type"], "id": q["id"], "address": q["address"], "client": q["client"], "date": q["date"],
              "time": q["window_start"], "suggested": q["employee"], "instruments": q["planned_count"]}
             for q in data["queue"]]
    trips = [{"id": t["id"], "title": t["title"], "lead": t["lead"], "dates": [t["date_start"], t["date_end"]]}
             for t in data["trips"]]
    out = {"period": [data["date_from"], data["date_to"]], "unassigned_queue": queue, "by_employee": by_emp,
           "trips": trips}
    return ToolResult(_dump(out), {"sources": [_source("ei", "Карта выездов")]})


def ei_team_schedule(ctx: ToolContext, args: dict) -> ToolResult:
    _require_control(ctx.user)
    today = timezone.localdate()
    start = _opt_date(args.get("date_from"), "date_from") or today
    end = _opt_date(args.get("date_to"), "date_to") or start + dt.timedelta(days=6)
    data = dispatch.team_schedule(start, end)
    rows = []
    for e in data["employees"]:
        cells = {}
        for day, c in e["cells"].items():
            bits = []
            if c["slots"]:
                bits.append("слоты " + ", ".join(
                    (f"{s['start']}–{s['end']}" if s["start"] else "весь день") + (" (командировка)" if s["kind"] == "trip" else "")
                    for s in c["slots"]))
            if c["stops"]:
                bits.append(f"адресов {c['stops']} (выполнено {c['done']})")
            if c["trips"]:
                bits.append("командировка: " + ", ".join(t["title"] for t in c["trips"]))
            if c["tasks"]:
                bits.append(f"задач со сроком {c['tasks']}")
            if c["lab"]:
                bits.append(f"приёмок {c['lab']}")
            cells[day] = "; ".join(bits)
        rows.append({"id": e["id"], "name": e["full_name"], "days": cells})
    return ToolResult(_dump({"period": [data["date_from"], data["date_to"]], "employees": rows}),
                      {"sources": [_source("ei", "Командный график")]})


def ei_staff(ctx: ToolContext, args: dict) -> ToolResult:
    _require_control(ctx.user)
    qs = Employee.objects.select_related("user").prefetch_related("attestations")
    if not args.get("include_inactive"):
        qs = qs.filter(is_active=True)
    rows = [staff.row(e) for e in qs.order_by("-is_active", "full_name")]
    out = {"can_admin": staff.can_admin(ctx.user), "roles": staff.roles(), "next_tab_number": staff.next_tab_number(),
           "staff": rows}
    return ToolResult(_dump(out), {"sources": [_source("ei", "Сотрудники")]})


# ===========================================================================
# Назначение
# ===========================================================================
def ei_assign_propose(ctx: ToolContext, args: dict) -> ToolResult:
    _require_control(ctx.user)
    employee = _employee(args.get("employee_id"))
    items = []
    for raw in args.get("items") or []:
        kind, pk = raw.get("type"), raw.get("id")
        if kind not in TYPE_LABEL or not isinstance(pk, int):
            raise ToolFailure("items[]: {type: stop|order|request, id} — id из ei_dispatch_board")
        items.append({"type": kind, "id": pk})
    if not items:
        raise ToolFailure("Нечего назначать: items пуст")
    if len(items) > 100:
        raise ToolFailure("За раз — до 100")
    date = _opt_date(args.get("date"), "date")
    time = _time(args.get("time"), "time")
    view = [["Кому", employee.full_name], ["Что", ", ".join(f"{TYPE_LABEL[i['type']]} №{i['id']}" for i in items)]]
    if date:
        view.append(["День", _d(date)])
    if time:
        view.append(["Время", time.strftime("%H:%M")])
    payload = {"items": items, "employee_id": employee.pk, "date": date.isoformat() if date else None,
               "time": time.strftime("%H:%M") if time else None}
    title = f"Назначить {len(items)} → {employee.full_name}"
    return _action(ctx, "dispatch_assign", title, payload, view,
                   "Адреса дня в городе переедут в день этого поверителя; заявки подтвердятся нарядом.")


def _dispatch_assign(action: PendingAction, user) -> dict:
    if not dispatch.can_control(user):
        raise PermissionDenied("Назначает руководство, метролог или менеджер")
    p = action.payload
    employee = Employee.objects.filter(pk=p["employee_id"], is_active=True).first()
    if employee is None:
        raise ActionError("Сотрудник уволен или удалён")
    date = dt.date.fromisoformat(p["date"]) if p.get("date") else None
    time = dt.time.fromisoformat(p["time"]) if p.get("time") else None
    result = dispatch.assign_many(p["items"], employee, date, time)
    if not result["done"]:
        raise ActionError("; ".join(e["error"] for e in result["errors"]) or "Ничего не назначено")
    return result


# ===========================================================================
# Сотрудники
# ===========================================================================
STAFF_OPS = {"create", "update", "deactivate", "activate", "reset_password"}


def ei_staff_propose(ctx: ToolContext, args: dict) -> ToolResult:
    _require_admin(ctx.user)
    op = args.get("op")
    if op not in STAFF_OPS:
        raise ToolFailure("op: create | update | deactivate | activate | reset_password")
    fields = {k: args.get(k) for k in ("full_name", "position", "email", "role", "tab_number") if args.get(k) not in (None, "")}
    if fields.get("role") and fields["role"] not in {r["value"] for r in staff.roles()}:
        raise ToolFailure("role: owner | head | metrologist | manager | verifier | observer")
    roles = dict((r["value"], r["label"]) for r in staff.roles())
    view: list = []
    if op == "create":
        if not fields.get("full_name"):
            raise ToolFailure("Для нового сотрудника нужно ФИО (full_name)")
        fields.setdefault("role", "verifier")
        view = [["ФИО", fields["full_name"]], ["Должность", fields.get("position", "—")],
                ["Почта для входа", fields.get("email") or "без входа в EI_doc"],
                ["Роль", roles[fields["role"]]], ["Табельный №", fields.get("tab_number") or staff.next_tab_number()]]
        title = f"Завести сотрудника: {fields['full_name']}"
        payload = {"op": op, **fields}
    else:
        employee = Employee.objects.select_related("user").filter(pk=args.get("employee_id")).first()
        if employee is None:
            raise ToolFailure("employee_id — из ei_staff")
        current = staff.row(employee)
        view = [["Сотрудник", employee.full_name]]
        if op == "update":
            if not fields:
                raise ToolFailure("Что поменять: full_name, position, email, role, tab_number")
            names = {"full_name": "ФИО", "position": "Должность", "email": "Почта", "role": "Роль", "tab_number": "Табельный №"}
            for key, value in fields.items():
                old = current.get(key) or "—"
                new = roles.get(value, value) if key == "role" else value
                old = current.get("role_display") if key == "role" else old
                view.append([names[key], f"{old} → {new}"])
            title = f"Изменить сотрудника: {employee.full_name}"
        elif op == "deactivate":
            view.append(["Что будет", "Уволен: пропадёт из списков назначения, вход в EI_doc закрыт. История остаётся."])
            title = f"Уволить: {employee.full_name}"
        elif op == "activate":
            view.append(["Что будет", "Снова в списках назначения, вход открыт"])
            title = f"Вернуть сотрудника: {employee.full_name}"
        else:
            if not current["email"]:
                raise ToolFailure("У сотрудника нет учётной записи — сначала op=update email=…")
            view.append(["Почта", current["email"]])
            view.append(["Что будет", "Новый временный пароль — покажется в карточке после подтверждения"])
            title = f"Сбросить пароль: {employee.full_name}"
        payload = {"op": op, "employee_id": employee.pk, **fields}
    return _action(ctx, "staff_change", title, payload, view,
                   "Временный пароль (если будет) человек увидит в карточке — тебе он не показывается.")


def _staff_change(action: PendingAction, user) -> dict:
    p = dict(action.payload)
    op = p.pop("op")
    p.pop("view", None)
    try:
        if op == "create":
            employee, secret = staff.create(
                user, full_name=p.get("full_name", ""), position=p.get("position", ""), email=p.get("email", ""),
                role=p.get("role") or "verifier", tab_number=p.get("tab_number", ""),
            )
        else:
            employee = Employee.objects.select_related("user").filter(pk=p.pop("employee_id")).first()
            if employee is None:
                raise ActionError("Сотрудник удалён")
            if op == "reset_password":
                secret = staff.reset_password(user, employee)
            else:
                if op in ("deactivate", "activate"):
                    p["is_active"] = op == "activate"
                employee, secret = staff.update(user, employee, **p)
    except staff.StaffError as exc:
        raise ActionError(str(exc)) from exc
    result = {"op": op, "employee_id": employee.pk, "full_name": employee.full_name,
              "email": employee.user.email if employee.user_id else ""}
    if secret:
        result["password"] = secret
    return result


def _staff_done(r: dict) -> str:
    text = {"create": "Сотрудник заведён", "update": "Сотрудник изменён", "deactivate": "Сотрудник уволен",
            "activate": "Сотрудник возвращён", "reset_password": "Пароль сброшен"}[r["op"]]
    tail = " Временный пароль показан человеку в карточке — сам его не повторяй и не выдумывай." if r.get("password") else ""
    return f"{text}: {r['full_name']} (id {r['employee_id']}{', ' + r['email'] if r['email'] else ''}).{tail}"


# ===========================================================================
# Регистрация
# ===========================================================================
EXECUTORS = {"dispatch_assign": _dispatch_assign, "staff_change": _staff_change}

DONE_TEXT = {
    "dispatch_assign": lambda r: (
        f"Назначено {len(r['done'])}: " + "; ".join(d["text"] for d in r["done"])
        + (f". Не вышло {len(r['errors'])}: " + "; ".join(e["error"] for e in r["errors"]) if r["errors"] else "")
        + "."
    ),
    "staff_change": _staff_done,
}

TOOLS: list[Tool] = [
    Tool(
        "ei_team", "Смотрю, кто чем занят",
        "Панель руководства на день (по умолчанию сегодня): итоги по фирме (адреса сегодня, заявки без исполнителя, "
        "выезды без даты, приёмки к выдаче, протоколы на нормоконтроле, просроченные задачи) и карточка каждого "
        "сотрудника: где он (командировка / выезды в городе / свободен / без слотов), адреса за день, приёмки, "
        "черновики, задачи. Список attention — что требует решения. Только руководство, метролог, менеджер.",
        _obj({"date": DATE}), ei_team,
    ),
    Tool(
        "ei_dispatch_board", "Смотрю карту выездов",
        "Карта выездов за период (по умолчанию сегодня): адреса по сотрудникам с stop_id, окнами и статусом, "
        "командировки, и очередь неназначенного — заявки с сайта (type=request) и выезды без даты (type=order). "
        "Отсюда берутся id для ei_assign_propose.",
        _obj({"date_from": DATE, "date_to": DATE}), ei_dispatch_board,
    ),
    Tool(
        "ei_team_schedule", "Смотрю командный график",
        "Сотрудники × дни (по умолчанию неделя от сегодня): слоты доступности, адреса, командировки, задачи со "
        "сроком, приёмки. Подбирать, кто свободен в нужный день. Слоты конкретного сотрудника меняет "
        "ei_availability_propose с employee_id.",
        _obj({"date_from": DATE, "date_to": DATE}), ei_team_schedule,
    ),
    Tool(
        "ei_assign_propose", "Готовлю назначение",
        "Назначить работу сотруднику — одна карточка на пачку (до 100). items[]: {type: stop (адрес выезда, id из "
        "ei_dispatch_board) | order (наряд без даты или приёмка, id наряда) | request (заявка с сайта — подтвердится "
        "нарядом), id}. employee_id — кому; date — день (адрес дня в городе переедет в день этого поверителя, у "
        "командировки — в её пределах); time HH:MM — начало окна визита (окно 2 часа). Отмеченные адреса не "
        "переназначаются.",
        _obj({"items": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["stop", "order", "request"]}, "id": INT}}},
            "employee_id": INT, "date": DATE, "time": TIME}, ["items", "employee_id"]),
        ei_assign_propose,
    ),
    Tool(
        "ei_staff", "Смотрю сотрудников",
        "Сотрудники с учётными записями: табельный №, должность, почта, роль, есть ли вход, Telegram, аттестация "
        "(действует ли, ближайший срок). include_inactive=true — и уволенные. can_admin — может ли пользователь "
        "менять сотрудников.",
        _obj({"include_inactive": BOOL}), ei_staff,
    ),
    Tool(
        "ei_staff_propose", "Готовлю изменение сотрудника",
        "Администрирование сотрудников — карточка с кнопкой (только владелец и руководитель). op=create: full_name "
        "(обязательно), position, email (даёт вход в EI_doc и временный пароль; без почты — сотрудник без входа), "
        "role: owner|head|metrologist|manager|verifier|observer (по умолчанию verifier), tab_number (по умолчанию "
        "следующий свободный). op=update, employee_id + что поменять (full_name, position, email, role, "
        "tab_number). op=deactivate — уволить (вход закрыт, история остаётся), op=activate — вернуть, "
        "op=reset_password — новый временный пароль. Владельцем делает только владелец; себя не уволить и свою роль "
        "не поменять. Пароль ты не видишь — его покажет карточка.",
        _obj({"op": {"type": "string", "enum": sorted(STAFF_OPS)}, "employee_id": INT, "full_name": STR,
              "position": STR, "email": STR, "role": STR, "tab_number": STR}, ["op"]),
        ei_staff_propose,
    ),
]
