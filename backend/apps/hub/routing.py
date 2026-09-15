"""Авто-роутинг заявок по району.

Заявка приходит с районом, уже распознанным подсказкой адреса (apps.hub.address)
или проставленным диспетчером вручную. Роутинг только предлагает исполнителя —
он не назначает: подтверждение и создание наряда — отдельное, осознанное
действие диспетчера (apps.hub.api.RequestConfirmView).
"""

from __future__ import annotations

from django.utils import timezone

from apps.catalog.models import District
from apps.hub.models import DistrictAssignment, Request, RequestStatus


def resolve_district(name: str) -> District | None:
    """Найти или завести район по названию из ответа подсказки адреса."""
    name = (name or "").strip()
    if not name:
        return None
    district, _ = District.objects.get_or_create(name=name)
    return district


def route(request_obj: Request) -> Request:
    """Подобрать рекомендованного исполнителя по району закреплённой заявки.

    Подтверждённую или отклонённую заявку не трогает — там уже принято
    решение человеком, автоматика его не переигрывает.
    """
    if request_obj.status not in (RequestStatus.NEW, RequestStatus.ROUTED):
        return request_obj

    suggestion = None
    if request_obj.district_id:
        assignment = (
            DistrictAssignment.objects.filter(district_id=request_obj.district_id, is_active=True)
            .order_by("priority")
            .select_related("employee")
            .first()
        )
        suggestion = assignment.employee if assignment else None

    request_obj.suggested_employee = suggestion
    request_obj.status = RequestStatus.ROUTED
    request_obj.routed_at = timezone.now()
    request_obj.save(update_fields=["suggested_employee", "status", "routed_at"])
    return request_obj
