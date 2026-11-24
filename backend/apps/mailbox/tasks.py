"""Задачи почты фирмы — своя очередь Celery «mail» (контейнер mail-worker в
docker-compose): IMAP/SMTP Timeweb отвечают секундами и не должны задерживать
распознавание бланков и служебные письма в очереди по умолчанию."""

from __future__ import annotations

from celery import shared_task

QUEUE = "mail"


@shared_task(ignore_result=True, queue=QUEUE, name="apps.mailbox.tasks.sync_mailboxes")
def sync_mailboxes() -> dict:
    """Раз в минуту (beat) и по кнопке. Замок — по ящику (lock.box_lock)."""
    from apps.mailbox import conf
    from apps.mailbox.sync import sync_all

    if not conf.enabled():
        return {}
    return sync_all()


@shared_task(ignore_result=True, queue=QUEUE, name="apps.mailbox.tasks.push_flag")
def push_flag(message_id: int) -> None:
    """Флаги письма — на сервер (открыли письмо → \\Seen). Не вышло — уйдут с синхронизацией."""
    from apps.mailbox import conf
    from apps.mailbox.imap import Client, ImapError
    from apps.mailbox.lock import Busy, box_lock
    from apps.mailbox.models import FolderState, Message
    from apps.mailbox.sync import push_dirty_flags

    msg = Message.objects.filter(pk=message_id, flags_dirty=True, uid__isnull=False).first()
    box = conf.box(msg.box) if msg else None
    if msg is None or box is None or not box.configured:
        return
    name = FolderState.objects.filter(box=msg.box, folder=msg.folder).values_list("imap_name", flat=True).first()
    if not name:
        return
    try:
        with box_lock(box.name), Client(box) as c:
            c.select(name)
            push_dirty_flags(c, box.name, msg.folder)
    except (Busy, ImapError):
        pass  # синхронизация сама отправит флаги (push_dirty_flags) на ближайшем тике


@shared_task(ignore_result=True, queue=QUEUE, name="apps.mailbox.tasks.deliver_mailbox_outbox")
def deliver_mailbox_outbox() -> int:
    """Письма из ящиков фирмы — по одному (apps.notify.outbox.deliver_mailbox)."""
    from apps.notify.outbox import deliver_mailbox

    total = 0
    while (sent := deliver_mailbox()) is not None:
        total += sent
    return total
