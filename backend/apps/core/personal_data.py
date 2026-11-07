"""Персональные данные по 152-ФЗ: сроки хранения, обезличивание, сведения субъекту.

Что считаем ПДн: контакты заявителя в заявке (имя, телефон, email, IP,
комментарий), клиент-физлицо (ФИО, телефон, email), адрес объекта частного
лица. Обезличивание заменяет их на «обезличено» и не удаляет записи —
номера, суммы и связи с поверками остаются, учёт не ломается.

Что не трогаем: подписанные протоколы и сведения в ФИФ. Их хранение —
обязанность поверителя по закону об обеспечении единства измерений, а это
самостоятельное основание обработки (п. 2 ч. 1 ст. 6 152-ФЗ). Об этом
сказано в политике (apps/core/templates/core/privacy.html).
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import PersonalDataEvent

MASK = "обезличено"


def _log(action: str, obj, note: str = "", user=None) -> None:
    PersonalDataEvent.objects.create(
        action=action, object_type=obj._meta.label_lower, object_id=obj.pk, note=note[:250],
        performed_by=user if getattr(user, "pk", None) else None,
    )


@transaction.atomic
def anonymize_request(request, *, action: str, note: str = "", user=None) -> bool:
    if request.anonymized_at:
        return False
    request.contact_name = MASK
    request.contact_phone = ""
    request.contact_email = ""
    request.ip_address = None
    request.comment = ""
    request.si_description = ""
    request.anonymized_at = timezone.now()
    request.save(update_fields=["contact_name", "contact_phone", "contact_email", "ip_address", "comment",
                                "si_description", "anonymized_at"])
    _log(action, request, note, user)
    return True


@transaction.atomic
def anonymize_client(client, *, action: str, note: str = "", user=None) -> bool:
    """Обезличить клиента-физлицо. Юрлицо — не ПДн, его не трогаем."""
    from apps.verification.models import ClientKind

    if client.anonymized_at or client.kind != ClientKind.PERSON:
        return False
    client.name = "Частное лицо"
    client.phone = ""
    client.email = ""
    client.note = ""
    client.anonymized_at = timezone.now()
    client.save(update_fields=["name", "phone", "email", "note", "anonymized_at"])
    for site in client.sites.all():
        site.contact = ""
        site.access_note = ""
        site.save(update_fields=["contact", "access_note"])
    _log(action, client, note, user)
    return True


def purge_expired(*, today: dt.date | None = None, dry_run: bool = False) -> dict:
    """Обезличить заявки, чей срок хранения истёк. Для Celery beat — раз в сутки."""
    from apps.hub.models import Request, RequestStatus

    now = timezone.now() if today is None else timezone.make_aware(dt.datetime.combine(today, dt.time()))
    short = now - dt.timedelta(days=settings.PD_RETENTION_REJECTED_DAYS)
    long = now - dt.timedelta(days=settings.PD_RETENTION_DAYS)

    rejected = Request.objects.filter(
        anonymized_at__isnull=True, status__in=[RequestStatus.REJECTED, RequestStatus.SPAM], created_at__lt=short,
    )
    finished = Request.objects.filter(anonymized_at__isnull=True, created_at__lt=long).filter(
        Q(status__in=[RequestStatus.DONE, RequestStatus.CANCELLED])
        | Q(status__in=[RequestStatus.NEW, RequestStatus.ROUTED])  # брошенные заявки
    )
    result = {"rejected": rejected.count(), "finished": finished.count()}
    if dry_run:
        return result
    for request in [*rejected, *finished]:
        anonymize_request(request, action=PersonalDataEvent.Action.ANONYMIZE_EXPIRED,
                          note=f"срок хранения истёк, статус «{request.get_status_display()}»")
    return result


def find_subject(*, phone: str = "", email: str = "") -> dict:
    """Что о человеке хранится — для ответа на запрос субъекта (ст. 14 152-ФЗ)."""
    from apps.hub.models import Request
    from apps.verification.models import Client

    digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    email = email.strip().lower()
    if not digits and not email:
        return {"requests": [], "clients": []}

    def matches(phone_value: str, email_value: str) -> bool:
        # Телефоны записаны как угодно («+7 (912) 345-67-89», «8912…») —
        # сравниваем последние 10 цифр, а не строку.
        normalized = "".join(ch for ch in phone_value or "" if ch.isdigit())[-10:]
        return bool(digits and normalized == digits) or bool(email and (email_value or "").lower() == email)

    # Запрос субъекта — редкая ручная операция: перебор по базе приемлем.
    requests = [r for r in Request.objects.filter(anonymized_at__isnull=True).iterator()
                if matches(r.contact_phone, r.contact_email)]
    clients = [c for c in Client.objects.filter(anonymized_at__isnull=True).iterator()
               if matches(c.phone, c.email)]
    return {
        "requests": [
            {"id": r.pk, "created_at": r.created_at, "status": r.get_status_display(), "name": r.contact_name,
             "phone": r.contact_phone, "email": r.contact_email, "address": r.address,
             "consent_at": r.consent_at, "consent_policy_version": r.consent_policy_version}
            for r in requests
        ],
        "clients": [
            {"id": c.pk, "kind": c.get_kind_display(), "name": c.name, "phone": c.phone, "email": c.email,
             "sites": [s.address for s in c.sites.all()],
             "verifications": c.instruments.count()}
            for c in clients
        ],
    }
