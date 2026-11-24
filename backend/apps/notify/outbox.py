"""Очередь писем: поставить (enqueue) и отправить фоном с повторами (deliver).

Как worker.go в openseller: письмо пишется в базу в той же транзакции, что
и действие, и не теряется, если SMTP сейчас не отвечает или процесс
перезапустили. Повторы — через 1, 2, 4 … минут, не реже раза в час; после
MAX_ATTEMPTS неудач — «не отправлено». Адрес, который сервер отверг (5xx),
не повторяем.
"""

from __future__ import annotations

import datetime as dt
import logging
import smtplib

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.utils import timezone

from apps.notify.models import OutboxMail, OutboxStatus
from apps.notify.render import render

logger = logging.getLogger("apps.notify.mail")

MAX_ATTEMPTS = 8
BATCH = 20

# Письма с живой ссылкой входа: тело не храним дольше, чем нужно для отправки,
# и не отправляем, когда ссылка уже истекла (повторы при сбоях SMTP идут до часа).
SECRET_TEMPLATES = {"password_reset"}
WIPED = "[удалено после отправки: письмо содержало ссылку для сброса пароля]"


def _secret_expired(mail: OutboxMail) -> bool:
    if mail.template not in SECRET_TEMPLATES:
        return False
    ttl = dt.timedelta(seconds=getattr(settings, "PASSWORD_RESET_TIMEOUT", 3600))
    return timezone.now() - mail.created_at >= ttl


def _wipe(mail: OutboxMail) -> list[str]:
    """Затереть тело письма со ссылкой. Возвращает поля для update_fields."""
    if mail.template not in SECRET_TEMPLATES:
        return []
    mail.text, mail.html = WIPED, ""
    return ["text", "html"]


def mail_configured() -> bool:
    return bool(getattr(settings, "SMTP_HOST", ""))


def backoff(attempt: int) -> dt.timedelta:
    minutes = 1 << max(attempt - 1, 0)
    return dt.timedelta(minutes=min(minutes, 60))


def enqueue(template: str, to: str, context: dict, *, user=None) -> OutboxMail | None:
    """Поставить письмо в очередь. Пустой адрес — тихо ничего (у наблюдателя может не быть почты)."""
    to = (to or "").strip()
    if not to:
        return None
    msg = render(template, context)
    mail = OutboxMail.objects.create(
        to=to, subject=msg.subject[:255], text=msg.text, html=msg.html, template=template, user=user,
        next_attempt_at=timezone.now(),
    )
    transaction.on_commit(kick)
    return mail


def kick() -> None:
    """Разбудить отправку. Нет брокера — не страшно: подберёт периодическая задача."""
    from apps.notify.tasks import deliver_outbox

    if not getattr(settings, "NOTIFY_ASYNC", True):
        deliver_outbox()
        return
    try:
        deliver_outbox.delay()
    except Exception:  # noqa: BLE001 — Redis недоступен: письмо уже в базе, уйдёт позже
        logger.warning("Не удалось поставить отправку почты в Celery — письмо уйдёт с периодической задачей")


def _permanent(exc: Exception) -> bool:
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return all(code >= 500 for code, _ in exc.recipients.values())
    if isinstance(exc, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return getattr(exc, "smtp_code", 0) >= 500
    return False


def _claim(limit: int) -> list[OutboxMail]:
    now = timezone.now()
    with transaction.atomic():
        rows = list(
            OutboxMail.objects.select_for_update(skip_locked=True)
            .filter(status=OutboxStatus.PENDING, next_attempt_at__lte=now)
            .order_by("next_attempt_at", "id")[:limit]
        )
        # «Аренда» на 5 минут: второй воркер те же письма не возьмёт, упавший — отпустит сам.
        OutboxMail.objects.filter(pk__in=[m.pk for m in rows]).update(next_attempt_at=now + dt.timedelta(minutes=5))
    return rows


def deliver(limit: int = BATCH) -> int:
    """Отправить, что готово. Возвращает, сколько ушло."""
    batch = _claim(limit)
    if not batch:
        return 0
    sent = 0
    connection = get_connection(fail_silently=False)
    try:
        connection.open()
    except Exception as exc:  # noqa: BLE001 — SMTP лежит: все письма пачки ждут следующей попытки
        logger.warning("SMTP недоступен: %s", exc)
        for mail in batch:
            _failed(mail, exc)
        return 0
    try:
        for mail in batch:
            if _secret_expired(mail):
                mail.status = OutboxStatus.FAILED
                mail.next_attempt_at = None
                mail.last_error = "Ссылка в письме истекла раньше, чем удалось отправить"
                mail.save(update_fields=["status", "next_attempt_at", "last_error", *_wipe(mail)])
                continue
            message = EmailMultiAlternatives(
                subject=mail.subject, body=mail.text, from_email=settings.DEFAULT_FROM_EMAIL,
                to=[mail.to], connection=connection,
            )
            if mail.html:
                message.attach_alternative(mail.html, "text/html")
            try:
                message.send()
            except Exception as exc:  # noqa: BLE001
                _failed(mail, exc)
                continue
            mail.status = OutboxStatus.SENT
            mail.sent_at = timezone.now()
            mail.attempts += 1
            mail.last_error = ""
            mail.save(update_fields=["status", "sent_at", "attempts", "last_error", *_wipe(mail)])
            sent += 1
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
    return sent


def _failed(mail: OutboxMail, exc: Exception) -> None:
    mail.attempts += 1
    mail.last_error = f"{type(exc).__name__}: {exc}"[:2000]
    if _permanent(exc) or mail.attempts >= MAX_ATTEMPTS:
        mail.status = OutboxStatus.FAILED
        mail.next_attempt_at = None
        wiped = _wipe(mail)
        logger.error("Письмо «%s» для %s не отправлено: %s", mail.subject, mail.to, mail.last_error)
    else:
        wiped = []
        mail.next_attempt_at = timezone.now() + backoff(mail.attempts)
        logger.warning("Письмо «%s» для %s — повтор через %s: %s", mail.subject, mail.to,
                       backoff(mail.attempts), mail.last_error)
    mail.save(update_fields=["attempts", "last_error", "status", "next_attempt_at", *wiped])
