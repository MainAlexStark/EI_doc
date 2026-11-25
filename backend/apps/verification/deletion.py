"""Удаление поверок, приёмок и нарядов — одни правила для API, ассистента и тестов.

Главное ограничение — нумерация протоколов. Подписанный, опубликованный в ФИФ или
аннулированный протокол (``ProtocolStatus.sealed``) уже ушёл главному метрологу и
в Аршин: его номер занят навсегда, поверку с ним удалить нельзя — только
аннулировать протокол в журнале. Черновик и протокол с присвоенным, но не
подписанным номером удаляются вместе с поверкой: номера пересчитаются при
следующем присвоении на нормоконтроле.

Кто может удалить:

* руководство, метролог и менеджер — любую поверку и приёмку без запечатанных протоколов;
* поверитель — свои поверки, пока нормоконтроль их не принял, и свою приёмку,
  если в ней нет принятых поверок.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from apps.core.models import Role
from apps.verification.models import Protocol, ProtocolStatus, Verification, VerificationStatus, WorkOrder

MANAGERS = {Role.OWNER, Role.HEAD, Role.METROLOGIST, Role.MANAGER}


class DeleteRefused(Exception):
    """Удалить нельзя — текст для человека."""


def is_manager(user) -> bool:
    return bool(user.is_superuser or user.role in MANAGERS)


def _employee_id(user) -> int | None:
    employee = getattr(user, "employee", None)
    return employee.pk if employee else None


def _sealed_protocol(verification: Verification) -> Protocol | None:
    protocol = Protocol.objects.filter(verification=verification).first()
    return protocol if protocol and protocol.status in ProtocolStatus.sealed() else None


def verification_refusal(verification: Verification, user) -> str:
    """Почему эту поверку удалить или поправить нельзя; пустая строка — можно."""
    protocol = _sealed_protocol(verification)
    if protocol is not None:
        number = f" № {protocol.full_number}" if protocol.full_number else ""
        return (
            f"Протокол{number} — «{protocol.get_status_display().lower()}», его номер уже занят. "
            "Поверку не удалить и не поправить — аннулируйте протокол в журнале"
        )
    if is_manager(user):
        return ""
    if verification.verifier_id != _employee_id(user):
        return "Чужую поверку удаляет и правит только руководство или менеджер"
    if verification.status == VerificationStatus.ACCEPTED:
        return "Поверку уже принял нормоконтроль — удалить её может только руководство или менеджер"
    return ""


def _drop_verification(verification: Verification) -> None:
    instrument = verification.instrument
    Protocol.objects.filter(verification=verification).delete()
    for photo in verification.photos.all():
        if photo.image:
            photo.image.delete(save=False)
    verification.delete()  # фото — каскадом, сканы бланков отвязываются (SET_NULL)
    # Экземпляр СИ, заведённый только ради этой поверки (часто — с опечаткой в номере),
    # не должен потом всплывать в подсказках «прошлые поверки у нас».
    if not instrument.verifications.exists():
        instrument.delete()


@transaction.atomic
def delete_verification(verification: Verification, user) -> None:
    reason = verification_refusal(verification, user)
    if reason:
        raise DeleteRefused(reason)
    order = verification.work_order
    _drop_verification(verification)
    if order is not None:
        order.refresh_status()


def order_refusal(order: WorkOrder, user) -> str:
    """Почему наряд (приёмку) удалить нельзя; пустая строка — можно."""
    from apps.logistics.models import TripStop

    if TripStop.objects.filter(work_order=order).exists():
        return "Это адрес командировки или выезда — удаляйте его там, где он запланирован"
    verifications = list(order.verifications.all())
    sealed = sum(1 for v in verifications if _sealed_protocol(v) is not None)
    if sealed:
        return (
            f"Есть подписанные протоколы ({sealed}) — их номера уже заняты, приёмку удалить нельзя. "
            "Удалить можно отдельные приборы без подписанных протоколов"
        )
    if is_manager(user):
        return ""
    if order.assigned_employee_id != _employee_id(user):
        return "Чужую приёмку удаляет только руководство или менеджер"
    if any(v.status == VerificationStatus.ACCEPTED for v in verifications):
        return "Есть поверки, принятые нормоконтролем, — удалить приёмку может только руководство или менеджер"
    return ""


@transaction.atomic
def delete_order(order: WorkOrder, user) -> None:
    reason = order_refusal(order, user)
    if reason:
        raise DeleteRefused(reason)
    from apps.logistics.models import StopStatus
    from apps.tasks.models import Task

    for verification in list(order.verifications.all()):
        _drop_verification(verification)
    for scan in order.scans.all():
        if scan.image:
            scan.image.delete(save=False)
    # Запланированный возврат приборов без самой приёмки теряет смысл; забор — уже был, остаётся.
    order.logistic_stops.filter(purpose="delivery").exclude(status=StopStatus.DONE).delete()
    # Задачи, привязанные к наряду, отвязываем — сами задачи остаются.
    Task.objects.filter(content_type=ContentType.objects.get_for_model(WorkOrder), object_id=order.pk).update(
        content_type=None, object_id=None
    )
    order.delete()
