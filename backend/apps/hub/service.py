"""Подтверждение и отклонение заявки — одна логика для экрана диспетчера и ассистента.

Вынесено из RequestConfirmView/RequestRejectView, чтобы карточка
ассистента («подтвердить заявку №12 на Иванова») делала ровно то же, что
кнопка на экране «Заявки», включая уведомление исполнителя (apps.notify).
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction

from apps.hub.models import Request, RequestStatus
from apps.verification.models import Client, ClientKind, Site, WorkOrder


class RequestActionError(Exception):
    pass


class AlreadyConfirmed(RequestActionError):
    """Заявку уже подтвердили (в т. ч. параллельно, пока этот запрос шёл)."""


def confirm_request(
    obj: Request, *, employee, scheduled_date: dt.date | None = None, scheduled_time: dt.time | None = None,
    client: Client | None = None, site: Site | None = None, note: str = "",
) -> WorkOrder:
    with transaction.atomic():
        # Блокировка строки: двойной клик / два диспетчера не заведут два наряда на одну заявку.
        locked = Request.objects.select_for_update().get(pk=obj.pk)
        if locked.status in (RequestStatus.CONFIRMED, RequestStatus.DONE, RequestStatus.CANCELLED):
            obj.status = locked.status
            raise AlreadyConfirmed("Заявка уже подтверждена")
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
        # Адрес заявки — сразу в день поверителя (вкладка «Выезды»), там карта и маршрут.
        from apps.logistics.service import attach_to_city_day

        attach_to_city_day(work_order)
    # Исполнителю напишет apps.notify.signals (назначение наряда) — после коммита.
    return work_order


def reject_request(obj: Request, *, reason: str = "", spam: bool = False) -> Request:
    obj.status = RequestStatus.SPAM if spam else RequestStatus.REJECTED
    obj.reject_reason = reason or ""
    obj.save(update_fields=["status", "reject_reason"])
    return obj

