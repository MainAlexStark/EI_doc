"""Подтверждение и отклонение заявки — одна логика для экрана диспетчера и ассистента.

Вынесено из RequestConfirmView/RequestRejectView, чтобы карточка
ассистента («подтвердить заявку №12 на Иванова») делала ровно то же, что
кнопка на экране «Заявки», включая уведомление исполнителя в Telegram.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction

from apps.hub.models import Request, RequestStatus
from apps.verification.models import Client, ClientKind, Site, WorkOrder


class RequestActionError(Exception):
    pass


def confirm_request(
    obj: Request, *, employee, scheduled_date: dt.date | None = None, scheduled_time: dt.time | None = None,
    client: Client | None = None, site: Site | None = None, note: str = "",
) -> WorkOrder:
    if obj.status == RequestStatus.CONFIRMED:
        raise RequestActionError("Заявка уже подтверждена")
    with transaction.atomic():
        if client is None:
            client = Client.objects.create(
                kind=ClientKind.PERSON, name=obj.contact_name, phone=obj.contact_phone, email=obj.contact_email,
            )
        if site is None:
            site = Site.objects.create(
                client=client, address=obj.address, postal_code=obj.postal_code, fias_id=obj.fias_id,
                district=obj.district, latitude=obj.latitude, longitude=obj.longitude,
            )
        work_order = WorkOrder.objects.create(
            request=obj, client=client, site=site, assigned_employee=employee,
            scheduled_date=scheduled_date or obj.desired_date,
            scheduled_time=scheduled_time or obj.desired_time,
            note=note or "",
        )
        obj.mark_confirmed(employee=employee)
    notify_work_order_assigned(work_order)
    return work_order


def reject_request(obj: Request, *, reason: str = "", spam: bool = False) -> Request:
    obj.status = RequestStatus.SPAM if spam else RequestStatus.REJECTED
    obj.reject_reason = reason or ""
    obj.save(update_fields=["status", "reject_reason"])
    return obj


def notify_work_order_assigned(work_order: WorkOrder) -> None:
    from apps.core.telegram import send_message

    employee = work_order.assigned_employee
    if not employee.telegram_chat_id:
        return
    when = work_order.scheduled_date.strftime("%d.%m.%Y") if work_order.scheduled_date else "дата не указана"
    if work_order.scheduled_time:
        when += f", {work_order.scheduled_time:%H:%M}"
    send_message(employee.telegram_chat_id, f"Новый наряд №{work_order.id}: {work_order.site.address}, {when}")
