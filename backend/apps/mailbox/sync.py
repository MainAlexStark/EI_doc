"""Синхронизация ящиков по IMAP: раз в минуту (Celery beat, очередь «mail») и по кнопке.

По каждой папке (Входящие, Отправленные, Архив, Спам, Корзина) помним
UIDVALIDITY и последний UID: докачиваем только новое, не больше
MAILBOX_SYNC_BATCH писем за запуск на ящик (остальное — следующий тик),
last_uid сохраняется после каждого письма. Сменился UIDVALIDITY — папку
грузим заново. Первая загрузка — последние MAILBOX_INITIAL_LIMIT писем, о
них не уведомляем, пока она не закончится (FolderState.initial_done).

Пропавшее с сервера письмо не удаляем сразу, а отвязываем (uid=None,
detached_at): если оно найдётся в другой папке (перенесли в веб-почте) —
строка, переписка и файл сохранятся, уведомления не будет. Не нашлось за
DETACH_GRACE — удаляем (и .eml — сигналом). Один ящик синхронизирует один
процесс (lock.box_lock).
"""

from __future__ import annotations

import datetime as dt
import email.utils
import imaplib
import logging
import time
from collections import defaultdict

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.mailbox import conf
from apps.mailbox.conf import Box
from apps.mailbox.imap import Client, ImapError
from apps.mailbox.lock import Busy, box_lock
from apps.mailbox.models import Direction, Folder, FolderState, Message
from apps.mailbox.parse import parse
from apps.mailbox.store import already_seen, find_detached, fingerprint, save_message

logger = logging.getLogger("apps.mailbox")

SYNC_FOLDERS = [Folder.INBOX, Folder.SENT, Folder.ARCHIVE, Folder.JUNK, Folder.TRASH]
FLAGS_WINDOW = 500
DETACH_GRACE = dt.timedelta(minutes=15)


def _internaldate(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        t = imaplib.Internaldate2tuple(f'INTERNALDATE "{value}"'.encode())
    except Exception:  # noqa: BLE001
        t = None
    if t is None:
        try:
            return email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return dt.datetime.fromtimestamp(time.mktime(t), tz=dt.timezone.utc)


def _box_error(box: Box, text: str) -> None:
    state, _ = FolderState.objects.get_or_create(box=box.name, folder=Folder.INBOX)
    state.error = text[:2000]
    state.save(update_fields=["error"])


def sync_all() -> dict[str, str]:
    """Все ящики; ошибка одного не мешает остальным и видна в панели (FolderState.error)."""
    out = {}
    for box in conf.boxes():
        if not box.configured:
            continue
        try:
            with box_lock(box.name) as lock:
                out[box.name] = f"новых: {sync_box(box, lock=lock)}"
        except Busy:
            out[box.name] = "уже синхронизируется"
        except ImapError as exc:
            logger.warning("Почта %s: %s", box.address, exc)
            _box_error(box, str(exc))
            out[box.name] = str(exc)
        except Exception as exc:  # noqa: BLE001 — сбой одного ящика не роняет остальные
            logger.exception("Почта %s: сбой синхронизации", box.address)
            _box_error(box, f"Сбой синхронизации: {type(exc).__name__}: {exc}")
            out[box.name] = f"сбой: {exc}"
    return out


def sync_box(box: Box, client: Client | None = None, lock=None) -> int:
    """Два прохода: сначала по всем папкам отмечаем пропавшее (отвязываем), потом
    забираем новое. Так письмо, перенесённое в веб-почте из Архива во Входящие,
    находит свою старую строку, в какой бы папке оно ни лежало раньше."""
    budget = [conf.sync_batch()]
    new_inbox: list[int] = []
    with (client or Client(box)) as c:
        names = c.folders()
        scanned: dict[str, tuple[FolderState, list[int]]] = {}
        for folder in SYNC_FOLDERS:
            name = names.get(folder)
            if not name:
                continue
            state, _ = FolderState.objects.get_or_create(box=box.name, folder=folder)
            try:
                scanned[folder] = (state, _scan_folder(c, box, folder, name, state))
            except ImapError as exc:
                _folder_error(box, state, exc)
        for folder, (state, server_uids) in scanned.items():
            try:
                ids = _fetch_folder(c, box, folder, state, server_uids, budget, lock)
            except ImapError as exc:
                _folder_error(box, state, exc)
                continue
            if folder == Folder.INBOX:
                new_inbox += ids
    purge_detached(box.name)
    if new_inbox:
        from apps.mailbox.notify import notify_new_mail

        notify_new_mail(new_inbox)
    return len(new_inbox)


def _folder_error(box: Box, state: FolderState, exc: Exception) -> None:
    state.error = str(exc)[:2000]
    state.save(update_fields=["error"])
    logger.warning("Почта %s/%s: %s", box.address, state.folder, exc)


def purge_detached(box: str) -> int:
    """Отвязанные и так и не нашедшиеся письма — удалить. Наши исходящие без UID
    (ещё не легли в «Отправленные») не трогаем: у них detached_at пуст."""
    stale = Message.objects.filter(box=box, uid__isnull=True, detached_at__lt=timezone.now() - DETACH_GRACE)
    n = 0
    for msg in stale:  # по одной — чтобы post_delete убрал .eml
        msg.delete()
        n += 1
    return n


def _scan_folder(c: Client, box: Box, folder: str, name: str, state: FolderState) -> list[int]:
    """Проход 1: UIDVALIDITY, список UID на сервере, отвязка пропавших."""
    uidvalidity = c.select(name)
    if state.uidvalidity is not None and state.uidvalidity != uidvalidity:
        # Папку пересоздали: старые UID ничего не значат — строки отвязываем
        # (найдутся по Message-ID — сохранятся), грузим папку заново.
        Message.objects.filter(box=box.name, folder=folder, uid__isnull=False).update(
            uid=None, uidvalidity=None, detached_at=timezone.now())
        state.last_uid, state.initial_done = 0, False
    state.uidvalidity, state.imap_name = uidvalidity, name
    state.save(update_fields=["uidvalidity", "imap_name", "last_uid", "initial_done"])
    server_uids = c.uids()
    server_set = set(server_uids)
    known = set(Message.objects.filter(box=box.name, folder=folder, uid__isnull=False)
                .values_list("uid", flat=True))
    gone = known - server_set
    if gone:
        Message.objects.filter(box=box.name, folder=folder, uid__in=gone).update(
            uid=None, uidvalidity=None, detached_at=timezone.now())
    return server_uids


def _fetch_folder(c: Client, box: Box, folder: str, state: FolderState, server_uids: list[int],
                  budget: list[int], lock) -> list[int]:
    """Проход 2: новые письма (не больше бюджета), наши флаги на сервер, серверные флаги к нам."""
    uidvalidity = c.select(state.imap_name)
    if uidvalidity != state.uidvalidity:
        raise ImapError("UIDVALIDITY сменился во время синхронизации — повторю на следующем тике")
    known = set(Message.objects.filter(box=box.name, folder=folder, uid__isnull=False)
                .values_list("uid", flat=True))
    if not state.initial_done and state.last_uid == 0 and len(server_uids) > conf.initial_limit():
        state.last_uid = server_uids[-conf.initial_limit()] - 1  # старше — не тянем
    pending = [u for u in server_uids if u > state.last_uid and u not in known]
    take = pending[:max(budget[0], 0)]
    meta = c.fetch_meta(take) if take else {}
    created: list[int] = []
    for uid in take:
        m = meta.get(uid)
        if m is not None:
            msg = _store_one(c, box, folder, uid, uidvalidity, m)
            if (msg is not None and state.initial_done and msg.direction == Direction.IN and not msg.seen
                    and not getattr(msg, "seen_before", False)):
                created.append(msg.pk)
        budget[0] -= 1
        state.last_uid = max(state.last_uid, uid)
        FolderState.objects.filter(pk=state.pk).update(last_uid=state.last_uid)
        if lock is not None and budget[0] % 20 == 0:
            lock.renew()
    backlog = len(pending) > len(take)

    push_dirty_flags(c, box.name, folder)
    _sync_flags(c, box, folder, sorted(u for u in server_uids if u in known)[-FLAGS_WINDOW:])

    state.synced_at, state.error = timezone.now(), ""
    if not backlog:
        state.initial_done = True
    state.save()
    return created


def push_dirty_flags(c: Client, box: str, folder: str) -> None:
    """Флаги, поменянные у нас (открыли письмо без брокера), — на сервер до чтения
    серверных, иначе синхронизация вернула бы письму «непрочитано»."""
    for msg in Message.objects.filter(box=box, folder=folder, flags_dirty=True, uid__isnull=False):
        c.store_flag(msg.uid, "\\Seen", msg.seen)
        c.store_flag(msg.uid, "\\Flagged", msg.flagged)
        Message.objects.filter(pk=msg.pk).update(flags_dirty=False)


def _sync_flags(c: Client, box: Box, folder: str, window: list[int]) -> None:
    """Флаги последних писем — прочитали/пометили в другой программе. Пачкой: UID
    группируются по набору флагов, обновляются только изменившиеся строки."""
    if not window:
        return
    groups: dict[tuple[bool, bool], list[int]] = defaultdict(list)
    for uid, m in c.fetch_meta(window).items():
        groups[("\\seen" in m.flags, "\\flagged" in m.flags)].append(uid)
    for (seen, flagged), uids in groups.items():
        (Message.objects.filter(box=box.name, folder=folder, uid__in=uids, flags_dirty=False)
         .exclude(seen=seen, flagged=flagged).update(seen=seen, flagged=flagged))


def _store_one(c: Client, box: Box, folder: str, uid: int, uidvalidity: int, m) -> Message | None:
    too_big = m.size > conf.max_bytes()
    raw = c.fetch_raw(uid, header_only=too_big)
    parsed = parse(raw)
    seen, flagged = "\\seen" in m.flags, "\\flagged" in m.flags
    fp = fingerprint(parsed, m.size)
    try:
        with transaction.atomic():
            # Строка этого письма без UID — наше исходящее до APPEND, перенесённое нами
            # или пропавшее из другой папки: подвязываем, а не заводим вторую.
            pending = find_detached(box.name, parsed.message_id, fp, folder)
            if pending is not None:
                pending.folder, pending.uid, pending.uidvalidity, pending.detached_at = folder, uid, uidvalidity, None
                pending.seen, pending.flagged = seen or pending.seen, flagged
                pending.save(update_fields=["folder", "uid", "uidvalidity", "detached_at", "seen", "flagged"])
                return None
            seen_before = already_seen(box.name, parsed.message_id, fp)
            msg = save_message(
                box=box.name, folder=folder, raw=raw, parsed=parsed, uid=uid, uidvalidity=uidvalidity,
                seen=seen, flagged=flagged, received_at=_internaldate(m.internaldate), size=m.size,
                too_big=too_big, box_address=box.address,
            )
    except IntegrityError:
        # Та же пара (папка, UID) уже есть — например, параллельная синхронизация до замка.
        logger.warning("Почта %s/%s: UID %s уже сохранён — пропускаю", box.address, folder, uid)
        return None
    msg.auto = parsed.auto_submitted  # автоответ/рассылка о себе не уведомляет
    msg.seen_before = seen_before or msg.auto
    return msg
