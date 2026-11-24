"""Действия с письмом: прочитано/важно и перенос — сначала на сервере, потом у себя.

Сервер — источник правды: если IMAP ответил ошибкой, локально ничего не
меняем и показываем человеку причину. Письмо без UID (наше, ещё не легло в
«Отправленные» на сервере) меняется только у себя.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.mailbox import conf
from apps.mailbox.imap import Client, ImapError
from apps.mailbox.models import Folder, FolderState, Message

logger = logging.getLogger("apps.mailbox")

MOVES = {
    "archive": Folder.ARCHIVE,
    "spam": Folder.JUNK,
    "trash": Folder.TRASH,
    "inbox": Folder.INBOX,  # «вернуть во входящие» — из архива, спама, корзины
}
FLAGS = {"read": ("\\Seen", "seen", True), "unread": ("\\Seen", "seen", False),
         "flag": ("\\Flagged", "flagged", True), "unflag": ("\\Flagged", "flagged", False)}
ACTIONS = set(MOVES) | set(FLAGS) | {"delete"}


class ActionError(Exception):
    pass


def _client(msg: Message, client_factory=None) -> Client:
    box = conf.box(msg.box)
    if box is None or not box.configured:
        raise ActionError("Ящик больше не настроен на сервере")
    return (client_factory or Client)(box)


def _folder_name(msg: Message, folder: str) -> str:
    name = FolderState.objects.filter(box=msg.box, folder=folder).values_list("imap_name", flat=True).first()
    if not name:
        raise ActionError("Папка ещё не синхронизирована — нажмите «Проверить почту»")
    return name


def apply(msg: Message, action: str, *, client_factory=None) -> Message:
    if action not in ACTIONS:
        raise ActionError("Неизвестное действие")
    if action in FLAGS:
        flag, field, on = FLAGS[action]
        if getattr(msg, field) == on:
            return msg
        if msg.uid is not None:
            _on_server(msg, client_factory, lambda c: c.store_flag(msg.uid, flag, on))
        setattr(msg, field, on)
        msg.save(update_fields=[field])
        return msg
    if action == "delete":
        if msg.folder != Folder.TRASH:
            raise ActionError("Насовсем удаляется только из корзины")
        if msg.uid is not None:
            erased = _on_server(msg, client_factory, lambda c: c.delete(msg.uid))
            if not erased:
                logger.info("Почта %s: сервер без UIDPLUS — письмо помечено \\Deleted, сотрёт сам сервер", msg.box)
        msg.delete()  # .eml убирает post_delete (signals.py)
        return msg
    target = MOVES[action]
    if msg.folder == target:
        return msg
    new_uid = None
    if msg.uid is not None:
        target_name = _folder_name(msg, target)
        new_uid = _on_server(msg, client_factory, lambda c: c.move(msg.uid, target_name))
    state = FolderState.objects.filter(box=msg.box, folder=target).first()
    msg.folder = target
    # Новый UID знаем, только если сервер сообщил COPYUID; иначе строку подвяжет
    # синхронизация по Message-ID (detached_at — чтобы её искали во всех папках).
    msg.uid = new_uid if new_uid and state and state.uidvalidity else None
    msg.uidvalidity = state.uidvalidity if msg.uid else None
    msg.detached_at = None if msg.uid else timezone.now()
    msg.save(update_fields=["folder", "uid", "uidvalidity", "detached_at"])
    return msg


def _on_server(msg: Message, client_factory, fn):
    source = _folder_name(msg, msg.folder)
    try:
        with _client(msg, client_factory) as c:
            c.select(source)
            return fn(c)
    except ImapError as exc:
        raise ActionError(f"Почтовый сервер не выполнил действие: {exc}") from exc
    except OSError as exc:  # на всякий случай: сетевое мимо обёртки Client
        raise ActionError(f"Нет связи с почтовым сервером: {exc}") from exc


def mark_read(msg: Message) -> None:
    """Открыли письмо — прочитано. У себя сразу, на сервер — фоновой задачей после
    коммита (GET не ходит в IMAP). Нет брокера — пропуск: следующая синхронизация
    увидит расхождение, но наш флаг сильнее — задача set_seen повторится с тиком."""
    if msg.seen:
        return
    msg.seen = True
    msg.flags_dirty = msg.uid is not None
    msg.save(update_fields=["seen", "flags_dirty"])
    if msg.uid is None:
        return

    def push():
        from apps.mailbox.tasks import push_flag

        try:
            push_flag.delay(msg.pk)
        except Exception:  # noqa: BLE001 — брокера нет: флаг уйдёт с ближайшей синхронизацией
            logger.info("Почта: нет брокера — \\Seen для письма %s уйдёт позже", msg.pk)

    transaction.on_commit(push)
