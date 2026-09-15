"""Авто-роутинг заявок по району.

Заявка приходит с районом, уже распознанным подсказкой адреса (apps.hub.address)
или проставленным диспетчером вручную. Роутинг только предлагает исполнителя —
он не назначает: подтверждение и создание наряда — отдельное, осознанное
действие диспетчера (apps.hub.api.RequestConfirmView).
"""

from __future__ import annotations

from django.utils import timezone

from apps.catalog.models import District
from apps.hub.models import AvailabilityKind, DistrictAssignment, EmployeeAvailability, Request, RequestStatus


def resolve_district(name: str) -> District | None:
    """Найти или завести район по названию из ответа подсказки адреса."""
    name = (name or "").strip()
    if not name:
        return None
    district, _ = District.objects.get_or_create(name=name)
    return district


def route(request_obj: Request) -> Request:
    """Подобрать рекомендованного исполнителя по району закреплённой заявки.

    Среди закреплённых за районом сотрудников (по возрастанию priority)
    сначала ищем того, у кого на желаемую дату заведён слот доступности
    (apps.hub.models.EmployeeAvailability, kind=district) — заявитель уже
    выбирал дату из предложенных на форме слотов, так рекомендация чаще
    совпадает с тем, кто реально свободен. Нет совпадения — как раньше,
    первый по приоритету закрепления; диспетчер всё равно может выбрать
    другого при подтверждении.

    Подтверждённую или отклонённую заявку не трогает — там уже принято
    решение человеком, автоматика его не переигрывает.
    """
    if request_obj.status not in (RequestStatus.NEW, RequestStatus.ROUTED):
        return request_obj

    suggestion = None
    if request_obj.district_id:
        assignments = list(
            DistrictAssignment.objects.filter(district_id=request_obj.district_id, is_active=True)
            .order_by("priority")
            .select_related("employee")
        )
        if request_obj.desired_date and assignments:
            available_ids = set(
                EmployeeAvailability.objects.filter(
                    employee_id__in=[a.employee_id for a in assignments],
                    kind=AvailabilityKind.DISTRICT,
                    date=request_obj.desired_date,
                ).values_list("employee_id", flat=True)
            )
            for assignment in assignments:
                if assignment.employee_id in available_ids:
                    suggestion = assignment.employee
                    break
        if suggestion is None and assignments:
            suggestion = assignments[0].employee

    request_obj.suggested_employee = suggestion
    request_obj.status = RequestStatus.ROUTED
    request_obj.routed_at = timezone.now()
    request_obj.save(update_fields=["suggested_employee", "status", "routed_at"])
    return request_obj
