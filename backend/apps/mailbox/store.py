"""Запись писем в базу и сборка переписок (как InsertMailboxMessage в openseller)."""

from __future__ import annotations

import datetime as dt
import hashlib
import re

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.mailbox.models import Direction, Folder, Message
from apps.mailbox.parse import Parsed

_RE_PREFIX = re.compile(r"^\s*((re|fwd?|aw|wg|ответ|отв|пересл)\s*(\[\d+\])?\s*:\s*)+", re.I)


def norm_subject(subject: str) -> str:
    return " ".join(_RE_PREFIX.sub("", subject or "").lower().split())


def thread_refs(in_reply_to: str, refs: str) -> list[str]:
    ids = re.findall(r"<[^<>\s]+>", f"{refs} {in_reply_to}")
    return list(dict.fromkeys(ids))[-50:]


def fingerprint(parsed: Parsed, size: int) -> str:
    """Письмо без Message-ID узнаём по дате, отправителю, теме и размеру."""
    key = f"{parsed.date.isoformat() if parsed.date else ''}|{parsed.from_addr}|{parsed.subject}|{size}"
    return hashlib.sha1(key.encode()).hexdigest()


def find_detached(box: str, message_id: str, fp: str, folder: str):
    """Строка этого письма без UID в любой папке ящика: наше исходящее до APPEND,
    письмо, которое мы перенесли, или пропавшее из другой папки (перенесли в веб-почте)."""
    from django.db.models import Q

    # Отвязанные (detached_at) — из любой папки; наше исходящее до APPEND (detached_at пуст) —
    # только в своей папке, иначе письмо самим себе «переехало» бы во Входящие.
    qs = Message.objects.select_for_update().filter(box=box, uid__isnull=True).filter(
        Q(detached_at__isnull=False) | Q(folder=folder))
    if message_id:
        return qs.filter(message_id=message_id).order_by("id").first()
    if fp:
        return qs.filter(message_id="", fingerprint=fp).order_by("id").first()
    return None


def already_seen(box: str, message_id: str, fp: str, exclude_pk: int | None = None) -> bool:
    """Письмо уже было в ящике (например, вернули из Архива во Входящие) — не «новое»."""
    qs = Message.objects.filter(box=box)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if message_id:
        return qs.filter(message_id=message_id).exists()
    return bool(fp) and qs.filter(message_id="", fingerprint=fp).exists()


def find_thread(box: str, parsed: Parsed, direction: str) -> int | None:
    """Переписка письма: по In-Reply-To/References, иначе — по теме с тем же собеседником за 30 дней."""
    ids = thread_refs(parsed.in_reply_to, parsed.refs)
    if ids:
        parent = (Message.objects.filter(box=box, message_id__in=ids).order_by("received_at", "id")
                  .values_list("thread_id", "id").first())
        if parent:
            return parent[0] or parent[1]
    subject = norm_subject(parsed.subject)
    if not subject or not ids:  # тема без «Re:» и без ссылок — новая переписка
        return None
    peer = parsed.from_addr if direction == Direction.IN else ""
    since = timezone.now() - dt.timedelta(days=30)
    qs = Message.objects.filter(box=box, received_at__gte=since).exclude(subject="")
    if peer:
        qs = qs.filter(to_addrs__icontains=peer) | qs.filter(from_addr=peer)
    for row in qs.order_by("-received_at").values("thread_id", "id", "subject")[:200]:
        if norm_subject(row["subject"]) == subject:
            return row["thread_id"] or row["id"]
    return None


def save_message(*, box: str, folder: str, raw: bytes, parsed: Parsed, uid: int | None, uidvalidity: int | None,
                 seen: bool, flagged: bool, received_at: dt.datetime | None, size: int, too_big: bool = False,
                 direction: str | None = None, box_address: str = "") -> Message:
    if direction is None:
        direction = Direction.OUT if (folder == Folder.SENT or parsed.from_addr == box_address) else Direction.IN
    with transaction.atomic():
        msg = Message(
            box=box, folder=folder, uid=uid, uidvalidity=uidvalidity, direction=direction,
            message_id=parsed.message_id, fingerprint=fingerprint(parsed, size or len(raw)),
            in_reply_to=parsed.in_reply_to, refs=parsed.refs[:20000],
            from_addr=parsed.from_addr, from_name=parsed.from_name, to_addrs=parsed.to, cc_addrs=parsed.cc,
            reply_to=parsed.reply_to, subject=parsed.subject, sent_at=parsed.date,
            received_at=received_at or parsed.date or timezone.now(), preview=parsed.preview,
            text=parsed.text[:200000], html=parsed.html[:500000], remote_images=parsed.remote_images,
            attachments=[a.as_dict() for a in parsed.attachments], size=size or len(raw), too_big=too_big,
            seen=seen, flagged=flagged,
        )
        msg.thread_id = find_thread(box, parsed, direction)
        msg.created_at = timezone.now()
        msg.raw.save(f"{uid or 'out'}-{timezone.now():%d%H%M%S%f}.eml", ContentFile(raw), save=False)
        try:
            msg.save()
        except Exception:
            msg.raw.delete(save=False)  # строки нет — и файл не нужен
            raise
        if msg.thread_id is None:
            Message.objects.filter(pk=msg.pk).update(thread_id=msg.pk)
            msg.thread_id = msg.pk
    return msg


def read_raw(msg: Message) -> bytes:
    if not msg.raw:
        return b""
    with msg.raw.open("rb") as fh:
        return fh.read()
