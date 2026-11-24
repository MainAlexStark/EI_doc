"""Где рождаются уведомления — на уровне моделей, а не в каждом экране.

Назначить выезд можно кнопкой в «Заявках», пачкой на карте, ассистентом,
в админке — сигнал ловит все пути сразу. Старое значение поля берём в
pre_save из базы: так «назначили» отличается от «сохранили что-то ещё».
Bulk update() сигналов не шлёт — назначений через него в коде нет.
"""

from __future__ import annotations

from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from apps.core.models import Role
from apps.hub.models import Request, RequestSource, RequestStatus
from apps.logistics.models import Trip, TripKind
from apps.notify.events import record, user_of, users_with_roles
from apps.notify.models import Category
from apps.tasks.models import Task
from apps.verification.models import Verification, VerificationStatus, WorkOrder

# Кому приходят новые заявки с сайта (каждый может выключить у себя в профиле).
REQUEST_RECIPIENTS = {Role.OWNER, Role.HEAD, Role.MANAGER, Role.OBSERVER}


_UNCHANGED = object()


def _old(sender, instance, field: str, update_fields=None):
    """Значение поля в базе до сохранения. Поле не сохраняется — _UNCHANGED, без запроса."""
    if update_fields is not None and field.removesuffix("_id") not in update_fields and field not in update_fields:
        return _UNCHANGED
    if not instance.pk:
        return None
    return sender.objects.filter(pk=instance.pk).values_list(field, flat=True).first()


def _telegram_unlinked(employee, text: str) -> None:
    """Сотрудник без учётной записи, но с Telegram — как раньше, сообщением сразу.
    Сводок у него нет: события копятся по пользователю, а пользователя нет."""
    if employee is None or not employee.is_active or getattr(employee, "user_id", None):
        return
    chat_id = getattr(employee, "telegram_chat_id", "")
    if not chat_id:
        return
    from django.db import transaction

    from apps.notify.events import _send_telegram

    transaction.on_commit(lambda: _send_telegram(chat_id, text))


def _same(instance, attr: str, value) -> bool:
    old = getattr(instance, attr, None)
    return old is _UNCHANGED or old == value


# --- заявка с сайта → руководству и наблюдателям --------------------------
@receiver(post_save, sender=Request)
def request_created(sender, instance: Request, created: bool, raw: bool = False, **kwargs) -> None:
    if raw or not created or instance.source != RequestSource.SITE:
        return
    if instance.honeypot_tripped or instance.status not in (RequestStatus.NEW, RequestStatus.ROUTED):
        return
    for user in users_with_roles(REQUEST_RECIPIENTS):
        record(user, Category.REQUESTS, "request_new", instance.pk)


# --- наряд (выезд по заявке, приёмка) → исполнителю -----------------------
@receiver(pre_save, sender=WorkOrder)
def _wo_before(sender, instance: WorkOrder, raw: bool = False, **kwargs) -> None:
    instance._notify_old_assignee = None if raw else _old(sender, instance, "assigned_employee_id", kwargs.get("update_fields"))


@receiver(post_save, sender=WorkOrder)
def work_order_assigned(sender, instance: WorkOrder, created: bool, raw: bool = False, **kwargs) -> None:
    if raw or not instance.assigned_employee_id:
        return
    if not created and _same(instance, "_notify_old_assignee", instance.assigned_employee_id):
        return
    employee = instance.assigned_employee
    record(user_of(employee), Category.ASSIGNMENTS, "work_order", instance.pk)
    when = instance.scheduled_date.strftime("%d.%m.%Y") if instance.scheduled_date else "дата не указана"
    if instance.scheduled_time:
        when += f", {instance.scheduled_time:%H:%M}"
    where = instance.site.address if instance.site_id else (instance.client.name if instance.client_id else "")
    _telegram_unlinked(employee, f"Новый наряд №{instance.pk}: {where}, {when}")


# --- командировка → старшему и участникам ---------------------------------
@receiver(pre_save, sender=Trip)
def _trip_before(sender, instance: Trip, raw: bool = False, **kwargs) -> None:
    instance._notify_old_lead = None if raw else _old(sender, instance, "lead_id", kwargs.get("update_fields"))


@receiver(post_save, sender=Trip)
def trip_lead(sender, instance: Trip, created: bool, raw: bool = False, **kwargs) -> None:
    if raw or instance.kind == TripKind.CITY:  # день в городе — служебный контейнер, о нём скажет наряд
        return
    if not created and _same(instance, "_notify_old_lead", instance.lead_id):
        return
    record(user_of(instance.lead), Category.ASSIGNMENTS, "trip", instance.pk)
    if instance.lead_id:
        _telegram_unlinked(instance.lead, f"Вы старший командировки «{instance.title}»")


@receiver(m2m_changed, sender=Trip.members.through)
def trip_members(sender, instance, action: str, reverse: bool, pk_set, **kwargs) -> None:
    if action != "post_add" or not pk_set:
        return
    from apps.core.models import Employee

    if reverse:  # employee.trips.add(trip)
        trips = Trip.objects.filter(pk__in=pk_set).exclude(kind=TripKind.CITY)
        for trip in trips:
            record(user_of(instance), Category.ASSIGNMENTS, "trip", trip.pk)
            _telegram_unlinked(instance, f"Вас добавили в командировку «{trip.title}»")
        return
    if instance.kind == TripKind.CITY:
        return
    for employee in Employee.objects.filter(pk__in=pk_set).select_related("user"):
        record(user_of(employee), Category.ASSIGNMENTS, "trip", instance.pk)
        _telegram_unlinked(employee, f"Вас добавили в командировку «{instance.title}»")


# --- задача → исполнителю -------------------------------------------------
@receiver(pre_save, sender=Task)
def _task_before(sender, instance: Task, raw: bool = False, **kwargs) -> None:
    instance._notify_old_assignee = None if raw else _old(sender, instance, "assignee_id", kwargs.get("update_fields"))


@receiver(post_save, sender=Task)
def task_assigned(sender, instance: Task, created: bool, raw: bool = False, **kwargs) -> None:
    if raw or not instance.assignee_id:
        return
    if not created and _same(instance, "_notify_old_assignee", instance.assignee_id):
        return
    record(user_of(instance.assignee), Category.ASSIGNMENTS, "task", instance.pk)
    when = f" к {instance.due_date:%d.%m.%Y}" if instance.due_date else ""
    _telegram_unlinked(instance.assignee, f"Новая задача: {instance.title}{when}")


# --- нормоконтроль вернул поверку → поверителю ----------------------------
@receiver(pre_save, sender=Verification)
def _verification_before(sender, instance: Verification, raw: bool = False, **kwargs) -> None:
    instance._notify_old_status = None if raw else _old(sender, instance, "status", kwargs.get("update_fields"))


@receiver(post_save, sender=Verification)
def verification_rejected(sender, instance: Verification, created: bool, raw: bool = False, **kwargs) -> None:
    if raw or instance.status != VerificationStatus.REJECTED:
        return
    if not created and _same(instance, "_notify_old_status", VerificationStatus.REJECTED):
        return
    record(user_of(instance.verifier), Category.REVIEW, "verification_rejected", instance.pk)


# --------------------------------------------------------------------------
# Смена пароля любым путём (профиль, ссылка, руководитель, Django-админка,
# manage.py changepassword) — запоминаем время: входы без метки пароля,
# выданные раньше, после этого не принимаются (apps.notify.actor).
# --------------------------------------------------------------------------
from django.conf import settings as _settings  # noqa: E402
from django.utils import timezone as _tz  # noqa: E402


@receiver(pre_save, sender=_settings.AUTH_USER_MODEL)
def _user_password_before(sender, instance, update_fields=None, **kwargs):
    instance._notify_password_changed = False
    if not instance.pk or (update_fields is not None and "password" not in update_fields):
        return
    old = sender.objects.filter(pk=instance.pk).values_list("password", flat=True).first()
    instance._notify_password_changed = old is not None and old != instance.password


@receiver(post_save, sender=_settings.AUTH_USER_MODEL)
def _user_password_after(sender, instance, created, **kwargs):
    if created or not getattr(instance, "_notify_password_changed", False):
        return
    from apps.notify.models import AccountSecurity

    AccountSecurity.objects.update_or_create(user=instance, defaults={"password_changed_at": _tz.now()})
    instance._notify_password_changed = False
