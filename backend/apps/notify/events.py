"""События → одно письмо (и одно сообщение в Telegram) на человека.

record() пишет NotifyEvent в той же транзакции, что и действие, и после
коммита будит сборку с задержкой NOTIFY_FLUSH_DELAY: назначили пачкой
30 адресов — человек получает одно письмо «Вам назначено: 30». flush()
перечитывает объекты на момент отправки: если наряд за эти секунды
переназначили другому или заявку уже разобрали — строка выпадает.

Единая точка для каналов: почта (если у человека есть адрес и категория
не выключена в профиле) и Telegram (если он привязан — apps.core.telegram).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.notify import outbox
from apps.notify.actor import current_actor_id
from apps.notify.models import Category, NotifyEvent, NotifyPrefs
from apps.notify.render import absolute

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Запись
# --------------------------------------------------------------------------
def record(user, category: str, kind: str, object_id: int | None = None, *, skip_actor: bool = True, **payload) -> None:
    if user is None or not getattr(user, "is_active", False):
        return
    actor = current_actor_id()
    if skip_actor and actor is not None and actor == user.pk:
        return  # сам себе назначил — писать незачем
    NotifyEvent.objects.create(
        user=user, category=category, kind=kind, object_id=object_id, payload=payload, actor_id=actor,
    )
    transaction.on_commit(kick)


def kick() -> None:
    from apps.notify.tasks import flush_events

    if not getattr(settings, "NOTIFY_ASYNC", True):
        flush_events()
        return
    try:
        flush_events.apply_async(countdown=getattr(settings, "NOTIFY_FLUSH_DELAY", 20))
    except Exception:  # noqa: BLE001 — брокер недоступен: события в базе, соберёт периодическая задача
        logger.warning("Не удалось поставить сборку уведомлений в Celery — соберёт периодическая задача")


def users_with_roles(roles) -> list:
    User = get_user_model()
    return list(User.objects.filter(is_active=True, role__in=list(roles)))


def user_of(employee):
    if employee is None:
        return None
    user = getattr(employee, "user", None)
    if user is None or not employee.is_active:
        return None
    return user


# --------------------------------------------------------------------------
# Строки сводки по видам событий
# --------------------------------------------------------------------------
@dataclass
class Line:
    text: str
    url: str = ""
    note: str = ""


def _fmt_date(value) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _line_request_new(ev: NotifyEvent) -> Line | None:
    from apps.hub.models import Request, RequestStatus

    obj = Request.objects.filter(pk=ev.object_id).select_related("district").prefetch_related("items__family").first()
    if obj is None or obj.status not in (RequestStatus.NEW, RequestStatus.ROUTED):
        return None
    items = ", ".join(
        f"{getattr(it.family, 'name', '')} × {it.quantity}" for it in obj.items.all()
    )
    when = _fmt_date(obj.desired_date)
    if when and obj.desired_time:
        when += f", {obj.desired_time:%H:%M}"
    parts = [p for p in (obj.address, items, f"желает {when}" if when else "") if p]
    text = f"Заявка №{obj.pk}: {obj.contact_name}"
    if obj.contact_phone:
        text += f", {obj.contact_phone}"
    return Line(text=text, note=" · ".join(parts), url=absolute("/#/requests"))


def _line_work_order(ev: NotifyEvent) -> Line | None:
    from apps.verification.models import WorkOrder, WorkOrderKind

    wo = WorkOrder.objects.filter(pk=ev.object_id).select_related("site", "client", "assigned_employee").first()
    if wo is None or wo.is_closed:
        return None
    employee = getattr(ev.user, "employee", None)
    if employee is None or wo.assigned_employee_id != employee.pk:
        return None  # за эти секунды переназначили другому
    if wo.kind == WorkOrderKind.LAB:
        return Line(text=f"Приёмка «{wo.client.name}»", note=f"заказ №{wo.pk}", url=absolute("/#/intake"))
    when = _fmt_date(wo.scheduled_date) or "дата не назначена"
    if wo.scheduled_time:
        when += f", {wo.scheduled_time:%H:%M}"
    return Line(text=f"Выезд: {wo.site.address}", note=f"наряд №{wo.pk} · {when}", url=absolute("/#/city"))


def _line_trip(ev: NotifyEvent) -> Line | None:
    from apps.logistics.models import Trip, TripKind

    trip = Trip.objects.filter(pk=ev.object_id).first()
    if trip is None or trip.kind == TripKind.CITY:
        return None
    employee = getattr(ev.user, "employee", None)
    if employee is None or (trip.lead_id != employee.pk and not trip.members.filter(pk=employee.pk).exists()):
        return None
    dates = "–".join(d for d in (_fmt_date(trip.date_start), _fmt_date(trip.date_end)) if d) or "даты не назначены"
    role = "старший" if trip.lead_id == employee.pk else "участник"
    url = absolute("/#/procurements" if trip.kind == TripKind.PROCUREMENT else "/#/trips")
    return Line(text=f"Командировка «{trip.title}»", note=f"{dates} · {role}", url=url)


def _line_task(ev: NotifyEvent) -> Line | None:
    from apps.tasks.models import Task, TaskStatus

    task = Task.objects.filter(pk=ev.object_id).first()
    if task is None or task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
        return None
    employee = getattr(ev.user, "employee", None)
    if employee is None or task.assignee_id != employee.pk:
        return None
    note = f"срок {_fmt_date(task.due_date)}" if task.due_date else ""
    return Line(text=f"Задача: {task.title}", note=note, url=absolute("/#/tasks"))


def _line_task_due(ev: NotifyEvent) -> Line | None:
    line = _line_task(ev)
    if line is None:
        return None
    from apps.tasks.models import Task

    task = Task.objects.filter(pk=ev.object_id).first()
    today = timezone.localdate()
    if task.due_date and task.due_date < today:
        line.note = f"просрочена с {_fmt_date(task.due_date)}"
    else:
        line.note = "срок сегодня"
    return line


def _line_verification_rejected(ev: NotifyEvent) -> Line | None:
    from apps.verification.models import Verification, VerificationStatus

    v = Verification.objects.filter(pk=ev.object_id).select_related("instrument").first()
    if v is None or v.status != VerificationStatus.REJECTED:
        return None
    return Line(
        text=f"Поверка «{v.instrument}» от {timezone.localtime(v.verified_at):%d.%m.%Y} возвращена на доработку",
        note=v.review_note or "", url=absolute("/#/journal"),
    )


def _line_review_queue(ev: NotifyEvent) -> Line | None:
    count = int(ev.payload.get("count") or 0)
    if not count:
        return None
    return Line(text=f"Ждут нормоконтроля: {count}", url=absolute("/#/journal"))


RENDERERS = {
    "request_new": _line_request_new,
    "work_order": _line_work_order,
    "trip": _line_trip,
    "task": _line_task,
    "task_due": _line_task_due,
    "verification_rejected": _line_verification_rejected,
    "review_queue": _line_review_queue,
}

TITLES = {
    Category.REQUESTS: ("Новая заявка с сайта", "Новые заявки с сайта: {n}"),
    Category.ASSIGNMENTS: ("Вам назначено", "Вам назначено: {n}"),
    Category.REVIEW: ("Нормоконтроль", "Нормоконтроль: {n}"),
    Category.REMINDERS: ("Сроки задач", "Сроки задач: {n}"),
}


def _title(category: str, n: int) -> str:
    one, many = TITLES.get(category, ("Уведомление", "Уведомления: {n}"))
    return one if n == 1 else many.format(n=n)


def _telegram_text(title: str, lines: list[Line]) -> str:
    rows = [title]
    for line in lines[:15]:
        rows.append(f"• {line.text}" + (f" — {line.note}" if line.note else ""))
    if len(lines) > 15:
        rows.append(f"…и ещё {len(lines) - 15}")
    return "\n".join(rows)


# --------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------
def flush(limit: int = 500, older_than: int = 0) -> int:
    """Свернуть неразосланные события в письма. Возвращает, сколько писем поставлено.

    older_than — только события старше стольких секунд (подстраховка не должна
    выхватывать свежие события раньше, чем их соберёт отложенная сборка).

    Каждая сводка (человек × категория) — своя транзакция: события
    блокируются, письмо ставится в очередь и события помечаются разосланными
    вместе. Упал воркер посреди цикла — необработанные группы остались
    неразосланными, их подберёт следующая сборка или подстраховка.
    Telegram — после коммита группы, ошибка одного человека не трогает остальных.
    """
    import datetime as dt

    qs = NotifyEvent.objects.filter(dispatched_at__isnull=True)
    if older_than:
        qs = qs.filter(created_at__lte=timezone.now() - dt.timedelta(seconds=older_than))
    candidates = list(qs.order_by("id").values_list("pk", "user_id", "category")[:limit])
    if not candidates:
        return 0
    groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    for pk, user_id, category in candidates:
        groups[(user_id, category)].append(pk)

    User = get_user_model()
    users = {u.pk: u for u in User.objects.filter(pk__in={k[0] for k in groups}).select_related("employee")}
    queued = 0
    for (user_id, category), pks in groups.items():
        telegram: tuple[str, str] | None = None
        with transaction.atomic():
            group = list(
                NotifyEvent.objects.select_for_update(skip_locked=True)
                .filter(pk__in=pks, dispatched_at__isnull=True).order_by("id")
            )
            if not group:
                continue  # забрала параллельная сборка
            user = users.get(user_id)
            lines = _lines(group) if user is not None and user.is_active else []
            if lines:
                title = _title(category, len(lines))
                queued += _email(user, category, title, lines)
                telegram = _telegram_target(user, title, lines)
            NotifyEvent.objects.filter(pk__in=[e.pk for e in group]).update(dispatched_at=timezone.now())
        if telegram:
            _send_telegram(*telegram)
    return queued


def _lines(group: list[NotifyEvent]) -> list[Line]:
    lines: list[Line] = []
    seen: set[tuple[str, int | None]] = set()
    for ev in group:
        key = (ev.kind, ev.object_id)
        if key in seen and ev.object_id is not None:
            continue
        seen.add(key)
        renderer = RENDERERS.get(ev.kind)
        try:
            line = renderer(ev) if renderer else None
        except Exception:  # noqa: BLE001 — одна битая строка не должна гасить всё письмо
            logger.exception("Не удалось собрать строку уведомления %s #%s", ev.kind, ev.object_id)
            line = None
        if line is not None:
            lines.append(line)
    return lines


def _telegram_target(user, title: str, lines: list[Line]) -> tuple[str, str] | None:
    employee = getattr(user, "employee", None)
    chat_id = getattr(employee, "telegram_chat_id", "") if employee else ""
    return (chat_id, _telegram_text(title, lines)) if chat_id else None


def _send_telegram(chat_id: str, text: str) -> None:
    from apps.core.telegram import send_message

    try:
        send_message(chat_id, text)
    except Exception:  # noqa: BLE001 — Telegram не должен ронять сборку остальных
        logger.exception("Не удалось отправить уведомление в Telegram (%s)", chat_id)


def _email(user, category: str, title: str, lines: list[Line], intro: str = "") -> int:
    if not (user.email and NotifyPrefs.wants_email(user, category)):
        return 0
    employee = getattr(user, "employee", None)
    name = (employee.full_name.split()[1] if employee and len(employee.full_name.split()) > 1
            else (employee.full_name if employee else ""))
    first_url = next((line.url for line in lines if line.url), absolute("/"))
    outbox.enqueue("digest", user.email, {
        "subject": f"{title} — EI_doc", "title": title, "intro": intro, "name": name,
        "lines": lines, "button_url": first_url, "button_text": "Открыть в EI_doc",
        "prefs_url": absolute("/#/profile"), "optional": category != Category.SECURITY,
    }, user=user)
    return 1


def deliver_to(user, category: str, title: str, lines: list[Line], intro: str = "") -> int:
    """Отправить одну сводку человеку по всем каналам. 1 — письмо поставлено в очередь."""
    target = _telegram_target(user, title, lines)
    if target:
        _send_telegram(*target)
    return _email(user, category, title, lines, intro)
