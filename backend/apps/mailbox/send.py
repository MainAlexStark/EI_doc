"""Письмо из ящика: собрать, поставить в общую очередь, отправить SMTP ящика,
положить копию в «Отправленные» (IMAP APPEND с \\Seen).

Очередь — та же apps.notify.outbox (повторы, «не отправлено» после 8 попыток),
у письма заполнен sender (имя ящика) — deliver() отдаёт его сюда (deliver_one).
Исходник .eml хранится у Message — тело в OutboxMail не дублируем.
"""

from __future__ import annotations

import datetime as dt
import logging
import mimetypes
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, getaddresses, make_msgid

from django.db import transaction
from django.utils import timezone

from apps.mailbox import conf
from apps.mailbox.conf import Box
from apps.mailbox.models import Direction, Folder, FolderState, Message
from apps.mailbox.parse import parse, part
from apps.mailbox.store import read_raw, save_message
from apps.notify.tlsmode import implicit_tls

logger = logging.getLogger("apps.mailbox")

MAX_ATTACH = 20 << 20
MAX_RCPTS = 20


class SendError(ValueError):
    """Текст — человеку как есть. В очереди — постоянная ошибка, без повтора."""


class MaybeSent(Exception):
    """Обрыв после того, как текст письма ушёл серверу: исход неизвестен, повторять нельзя."""


def clean_name(name: str) -> str:
    """Имя файла без переводов строк и управляющих символов (иначе заголовок письма сломается → 500)."""
    name = "".join(ch if ch.isprintable() else " " for ch in (name or ""))
    name = " ".join(name.replace("/", "_").replace("\\", "_").split())[:200]
    return name or "файл"


def clean_ctype(ctype: str) -> str:
    ctype = (ctype or "").split(";")[0].strip().lower()
    main, _, sub = ctype.partition("/")
    ok = main.isascii() and sub and all(ch.isalnum() or ch in "-+._" for ch in main + sub)
    return ctype if ok else "application/octet-stream"


def parse_rcpts(value: str) -> list[tuple[str, str]]:
    value = (value or "").replace(";", ",").strip()
    if not value:
        return []
    out = []
    for name, addr in getaddresses([value]):
        addr = addr.strip()
        if not addr or "@" not in addr or any(ch in addr for ch in " \r\n<>,"):
            raise SendError(f"Проверьте адрес «{addr or name}»")
        out.append((name.strip(), addr))
    return out


def quote_text(msg: Message) -> str:
    """Как quoteText в openseller: «дата, кто пишет:» и строки с «> »."""
    text = (msg.text or "").strip()
    if not text:
        return ""
    if len(text) > 8000:
        text = text[:8000] + "\n…"
    when = timezone.localtime(msg.sent_at or msg.received_at)
    who = f"{msg.from_name} <{msg.from_addr}>" if msg.from_name else msg.from_addr
    lines = [f"{when:%d.%m.%Y %H:%M}, {who} пишет:"]
    lines += ["> " + line for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines) + "\n"


def reply_refs(msg: Message) -> tuple[str, list[str]]:
    refs = msg.refs.split()
    if msg.message_id:
        refs.append(msg.message_id)
    if len(refs) > 20:  # длинная цепочка: первое письмо и последние
        refs = refs[:1] + refs[-19:]
    return msg.message_id, refs


def compose(box: Box, *, to, cc, subject: str, text: str, files, in_reply_to: str = "",
            refs: list[str] | None = None, now: dt.datetime | None = None) -> tuple[bytes, str]:
    now = now or timezone.now()
    m = EmailMessage()
    m["From"] = Address(box.from_name, addr_spec=box.address) if box.from_name else box.address
    m["To"] = ", ".join(str(Address(n, addr_spec=a)) if n else a for n, a in to)
    if cc:
        m["Cc"] = ", ".join(str(Address(n, addr_spec=a)) if n else a for n, a in cc)
    m["Subject"] = subject
    m["Date"] = format_datetime(now)
    msg_id = make_msgid(domain=box.domain)
    m["Message-ID"] = msg_id
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if refs:
        m["References"] = " ".join(refs)
    m.set_content(text)
    for name, ctype, data in files:
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    return m.as_bytes(), msg_id


def send(*, box_name: str, user, to: str, cc: str, subject: str, text: str, uploads=(),
         reply_to: Message | None = None, forward: Message | None = None) -> Message:
    box = conf.box(box_name)
    if box is None or not box.configured:
        raise SendError("Выберите, с какого ящика отправить")
    rcpt_to, rcpt_cc = parse_rcpts(to), parse_rcpts(cc)
    if not rcpt_to:
        raise SendError("Проверьте адрес получателя")
    if len(rcpt_to) + len(rcpt_cc) > MAX_RCPTS:
        raise SendError(f"Не больше {MAX_RCPTS} получателей")
    subject = " ".join((subject or "").split())[:900]
    text = (text or "").replace("\r\n", "\n")
    if not text.strip():
        raise SendError("Пустое письмо")
    files, total = [], 0
    for up in uploads:
        if not up.size:
            continue
        total += up.size
        if total > MAX_ATTACH:
            raise SendError("Вложения больше 20 МБ — так много за раз не отправить")
        ctype = getattr(up, "content_type", "") or mimetypes.guess_type(up.name)[0] or "application/octet-stream"
        files.append((clean_name(up.name), clean_ctype(ctype), up.read()))
    if forward is not None:  # пересылка: вложения исходного письма — с собой
        raw_src = read_raw(forward)
        for att in forward.attachments:
            if att.get("inline"):
                continue
            got = part(raw_src, att["n"])
            if got is None:
                continue
            total += len(got[1])
            if total > MAX_ATTACH:
                raise SendError("Вложения пересылаемого письма больше 20 МБ")
            files.append((clean_name(got[0].name), clean_ctype(got[0].type), got[1]))
    in_reply_to, refs = reply_refs(reply_to) if reply_to is not None else ("", [])
    raw, _ = compose(box, to=rcpt_to, cc=rcpt_cc, subject=subject, text=text, files=files,
                     in_reply_to=in_reply_to, refs=refs)

    from apps.notify.models import OutboxMail

    with transaction.atomic():
        out = OutboxMail.objects.create(
            to=rcpt_to[0][1], subject=subject[:255] or "(без темы)", text="", template="mailbox",
            sender=box.name, user=user, next_attempt_at=timezone.now(),
        )
        msg = save_message(box=box.name, folder=Folder.SENT, raw=raw, parsed=parse(raw), uid=None,
                           uidvalidity=None, seen=True, flagged=False, received_at=timezone.now(), size=len(raw),
                           direction=Direction.OUT, box_address=box.address)
        if reply_to is not None and msg.thread_id != reply_to.thread_root_id:
            msg.thread_id = reply_to.thread_root_id
        msg.outbox, msg.sent_by = out, user
        msg.save(update_fields=["thread", "outbox", "sent_by"])
        from apps.notify.outbox import kick_mailbox

        transaction.on_commit(kick_mailbox)
    return msg


# ---------------------------------------------------------------- доставка из очереди
def deliver_one(mail) -> None:
    """Отправить письмо ящика SMTP этого ящика. Исключение — повтор/«не отправлено» решает outbox."""
    msg = Message.objects.filter(outbox=mail).first()
    if msg is None:
        raise SendError("Исходник письма пропал")
    box = conf.box(mail.sender)
    if box is None or not box.configured:
        raise SendError(f"Ящик «{mail.sender}» не настроен")
    raw = read_raw(msg)
    rcpts = [a for _, a in getaddresses([msg.to_addrs, msg.cc_addrs]) if a]
    smtp_send(box, raw, rcpts)
    try:
        append_sent(box, raw)
    except Exception as exc:  # noqa: BLE001 — письмо ушло; копия в «Отправленных» — не повод слать снова
        logger.warning("Почта %s: письмо ушло, но копия в «Отправленные» не легла: %s", box.address, exc)


def smtp_send(box: Box, raw: bytes, rcpts: list[str]) -> None:
    """Подключение и вход — ошибки здесь безопасно повторять. После начала передачи
    (sendmail) обрыв/таймаут — MaybeSent: сервер мог уже принять письмо.
    Явный отказ сервера (SMTPRecipientsRefused/SenderRefused/DataError) — не «могло уйти»:
    решает outbox._permanent (5xx — без повтора, 4xx — повтор)."""
    ctx = ssl.create_default_context()
    if implicit_tls(box.smtp_port, box.smtp_tls):
        server = smtplib.SMTP_SSL(box.smtp_host, box.smtp_port, timeout=30, context=ctx)
    else:
        server = smtplib.SMTP(box.smtp_host, box.smtp_port, timeout=30)
        server.starttls(context=ctx)
    try:
        server.login(box.address, box.password)
        try:
            server.sendmail(box.address, rcpts, raw)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            raise
        except (smtplib.SMTPServerDisconnected, OSError, TimeoutError) as exc:
            raise MaybeSent(f"{type(exc).__name__}: {exc}") from exc
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def append_sent(box: Box, raw: bytes, client=None) -> None:
    from apps.mailbox.imap import Client

    name = FolderState.objects.filter(box=box.name, folder=Folder.SENT).values_list("imap_name", flat=True).first()
    with (client or Client(box)) as c:
        if not name:
            name = c.folders().get(Folder.SENT)
        if name:
            c.append(name, raw)
