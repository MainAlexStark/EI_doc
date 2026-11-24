"""Новое письмо во «Входящих» → руководству сводкой (почта + Telegram).

Как notifyNewMail в openseller: уведомление не уходит на адреса самих ящиков
фирмы (events._is_company_box — иначе петля), а служебные письма EI_doc
(X-EI-Doc-Notify / Auto-Submitted) и автоответы о себе не уведомляют
(sync помечает их seen_before). Все события — одной вставкой и одной сборкой.
"""

from __future__ import annotations

from django.db import transaction

from apps.mailbox.access import mail_users
from apps.notify.models import Category, NotifyEvent


def notify_new_mail(message_ids: list[int]) -> None:
    from apps.notify.events import kick

    users = mail_users()
    if not message_ids or not users:
        return
    with transaction.atomic():
        NotifyEvent.objects.bulk_create([
            NotifyEvent(user=user, category=Category.MAIL, kind="mail_new", object_id=pk, payload={})
            for pk in message_ids for user in users
        ])
        transaction.on_commit(kick)
