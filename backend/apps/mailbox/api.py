"""API панели «Почта» (/api/mail/…). Только владелец, руководитель, менеджер.

HTML письма (уже очищенный nh3) отдаётся отдельным документом с жёстким CSP
(как adminMailBody в openseller): без скриптов и форм, внешние картинки —
только по ?images=1. Фронт кладёт его в <iframe sandbox srcdoc> без
allow-scripts и без allow-same-origin: у документа уникальный «пустой» origin,
скрипты в нём не запускаются, к странице кабинета, JWT в localStorage и
cookie он доступа не имеет. CSP ещё и внутри документа (meta) — srcdoc
заголовки ответа не наследует.
"""

from __future__ import annotations

import html as htmllib
import threading
from email.utils import getaddresses

from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mailbox import actions, conf, dns
from apps.mailbox.access import CanMail
from apps.mailbox.models import Direction, Folder, FolderState, Message
from apps.mailbox.parse import inline_cids, part, safe_attachment_type
from apps.mailbox.send import SendError, quote_text, send
from apps.mailbox.store import read_raw

PAGE = 50
ENV_HINT = [
    "MAILBOXES=info=info@eicounters.ru",
    "MAILBOX_PASSWORD_INFO=пароль ящика",
    "# по умолчанию Timeweb: MAILBOX_IMAP_HOST=imap.timeweb.ru, MAILBOX_IMAP_PORT=993,",
    "# MAILBOX_SMTP_HOST=smtp.timeweb.ru, MAILBOX_SMTP_PORT=465",
]


def _box_rows() -> tuple[list[dict], int]:
    unread = dict(
        Message.objects.filter(folder=Folder.INBOX, direction=Direction.IN, seen=False)
        .values_list("box").annotate(n=Count("id")).values_list("box", "n")
    )
    states = {s.box: s for s in FolderState.objects.filter(folder=Folder.INBOX)}
    rows = []
    for b in conf.boxes():
        st = states.get(b.name)
        rows.append({
            "name": b.name, "address": b.address, "configured": b.configured, "unread": unread.get(b.name, 0),
            "synced_at": st.synced_at if st else None, "error": st.error if st else "",
        })
    return rows, sum(r["unread"] for r in rows)


def _who(m: Message) -> str:
    if m.direction == Direction.OUT:
        pairs = getaddresses([m.to_addrs])
        return ", ".join(n or a for n, a in pairs[:3]) or m.to_addrs
    return m.from_name or m.from_addr


def row(m: Message) -> dict:
    return {
        "id": m.pk, "box": m.box, "folder": m.folder, "direction": m.direction, "thread": m.thread_root_id,
        "who": _who(m), "from_addr": m.from_addr, "subject": m.subject, "preview": m.preview,
        "date": m.sent_at or m.received_at, "received_at": m.received_at, "unread": m.unread,
        "flagged": m.flagged, "has_attachments": any(not a.get("inline") for a in m.attachments),
    }


def detail(m: Message) -> dict:
    out = row(m)
    outbox = m.outbox
    out.update({
        "from_name": m.from_name, "to": m.to_addrs, "cc": m.cc_addrs, "reply_to": m.reply_to,
        "text": m.text, "has_html": bool(m.html), "remote_images": m.remote_images, "too_big": m.too_big,
        "size": m.size, "message_id": m.message_id,
        "attachments": [a for a in m.attachments if not a.get("inline")],
        "sending": None if outbox is None else {
            "status": outbox.status, "error": outbox.last_error, "attempts": outbox.attempts,
            "sent_at": outbox.sent_at,
        },
        "sent_by": getattr(getattr(m.sent_by, "employee", None), "full_name", "") if m.sent_by_id else "",
    })
    return out


class StatusView(APIView):
    """GET — ящики, непрочитанные, включено ли; ?dns=1 — ещё и записи домена."""

    permission_classes = [CanMail]

    def get(self, request):
        boxes, unread = _box_rows()
        data = {"enabled": conf.enabled(), "boxes": boxes, "unread": unread, "folders": Folder.choices,
                "env_hint": [] if conf.enabled() else ENV_HINT}
        if request.query_params.get("dns") == "1":
            data["dns"] = [dns.check(d) for d in dns.domains()]
        return Response(data)


class SyncView(APIView):
    """POST — «Проверить почту». Всегда фоном: в Celery (очередь «mail»), а без брокера —
    в потоке процесса; запрос в IMAP не ходит. Ящик, который уже синхронизируется,
    пропускается (lock.box_lock) — два нажатия подряд не запустят две синхронизации."""

    permission_classes = [CanMail]

    def post(self, request):
        from apps.mailbox.tasks import sync_mailboxes

        try:
            sync_mailboxes.delay()
            return Response({"queued": True}, status=status.HTTP_202_ACCEPTED)
        except Exception:  # noqa: BLE001 — брокера нет
            threading.Thread(target=_sync_in_thread, name="mailbox-sync", daemon=True).start()
            return Response({"queued": False}, status=status.HTTP_202_ACCEPTED)


def _sync_in_thread() -> None:
    from django.db import connection

    from apps.mailbox.sync import sync_all

    try:
        sync_all()
    finally:
        connection.close()  # своё соединение потока — не оставлять открытым


class MessageListView(APIView):
    """GET ?box=&folder=inbox|sent|archive|junk|trash|unread|flagged&q=&page="""

    permission_classes = [CanMail]

    def get(self, request):
        p = request.query_params
        qs = Message.objects.all()
        if box := p.get("box"):
            qs = qs.filter(box=box)
        folder = p.get("folder") or Folder.INBOX
        if folder == "unread":
            qs = qs.filter(folder=Folder.INBOX, direction=Direction.IN, seen=False)
        elif folder == "flagged":
            qs = qs.filter(flagged=True).exclude(folder=Folder.TRASH)
        elif folder in Folder.values:
            qs = qs.filter(folder=folder)
        else:
            return Response({"folder": ["Нет такой папки"]}, status=status.HTTP_400_BAD_REQUEST)
        if q := (p.get("q") or "").strip()[:200]:
            qs = qs.filter(Q(subject__icontains=q) | Q(from_addr__icontains=q) | Q(from_name__icontains=q)
                           | Q(to_addrs__icontains=q) | Q(cc_addrs__icontains=q) | Q(text__icontains=q))
        try:
            page = max(1, int(p.get("page") or 1))
        except ValueError:
            page = 1
        rows = list(qs.order_by("-received_at", "-id")[(page - 1) * PAGE: page * PAGE + 1])
        return Response({"results": [row(m) for m in rows[:PAGE]], "page": page, "has_more": len(rows) > PAGE})


class MessageDetailView(APIView):
    """GET — письмо и вся переписка; открыть = прочитано (и на сервере)."""

    permission_classes = [CanMail]

    def get(self, request, pk: int):
        msg = get_object_or_404(Message.objects.select_related("outbox", "sent_by__employee"), pk=pk)
        if request.query_params.get("keep_unread") != "1":
            actions.mark_read(msg)
        thread = list(
            Message.objects.filter(thread_id=msg.thread_root_id).select_related("outbox", "sent_by__employee")
            .order_by("received_at", "id")
        )
        return Response({"message": detail(msg), "thread": [detail(m) for m in thread]})


class MessageActionView(APIView):
    """POST {action: read|unread|flag|unflag|archive|spam|trash|inbox|delete}"""

    permission_classes = [CanMail]

    def post(self, request, pk: int):
        msg = get_object_or_404(Message, pk=pk)
        action = (request.data or {}).get("action", "")
        try:
            msg = actions.apply(msg, action)
        except actions.ActionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if action == "delete":
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(row(msg))


BODY_STYLE = ("body{margin:12px;font:14px/1.5 -apple-system,system-ui,sans-serif;color:#1c1c1e;background:#fff;"
              "overflow-wrap:anywhere}img{max-width:100%;height:auto}blockquote{margin:0 0 0 .6em;"
              "padding-left:.6em;border-left:2px solid #ccc;color:#555}")


def body_document(msg: Message, images: bool) -> str:
    img = "data: https: http:" if images else "data:"
    csp = (f"default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src {img}; "
           "form-action 'none'; base-uri 'none'")
    body = msg.html
    if body:
        try:
            body = inline_cids(read_raw(msg), body)
        except OSError:
            pass
    else:
        body = f"<pre style='white-space:pre-wrap;font:inherit'>{htmllib.escape(msg.text)}</pre>"
    return (f'<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{csp}">'
            f'<meta name="referrer" content="no-referrer"><base target="_blank"><style>{BODY_STYLE}</style>{body}')


class MessageBodyView(APIView):
    """GET — HTML письма для <iframe sandbox srcdoc>; ?images=1 — с внешними картинками."""

    permission_classes = [CanMail]

    def get(self, request, pk: int):
        msg = get_object_or_404(Message, pk=pk)
        doc = body_document(msg, request.query_params.get("images") == "1")
        resp = HttpResponse(doc, content_type="text/html; charset=utf-8")
        img = "data: https: http:" if request.query_params.get("images") == "1" else "data:"
        resp["Content-Security-Policy"] = (f"sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
                                           f"style-src 'unsafe-inline'; img-src {img}; form-action 'none'")
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Referrer-Policy"] = "no-referrer"
        resp["Cache-Control"] = "private, no-store"
        return resp


def _download(data: bytes, ctype: str, name: str) -> HttpResponse:
    from django.utils.http import content_disposition_header

    resp = HttpResponse(data, content_type=ctype)
    resp["Content-Disposition"] = content_disposition_header(True, name)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "sandbox; default-src 'none'"
    resp["Cache-Control"] = "private, no-store"
    return resp


class AttachmentView(APIView):
    """GET — вложение, всегда скачиванием: чужой HTML/SVG не откроется на нашем домене."""

    permission_classes = [CanMail]

    def get(self, request, pk: int, n: int):
        msg = get_object_or_404(Message, pk=pk)
        got = part(read_raw(msg), n)
        if got is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        att, data = got
        return _download(data, safe_attachment_type(att.type), att.name)


class RawView(APIView):
    permission_classes = [CanMail]

    def get(self, request, pk: int):
        msg = get_object_or_404(Message, pk=pk)
        return _download(read_raw(msg), "message/rfc822", f"mail-{msg.pk}.eml")


class ComposeView(APIView):
    """GET ?reply=<id>&mode=reply|all|forward — заготовка формы; без reply — пустая."""

    permission_classes = [CanMail]

    def get(self, request):
        boxes = [b.name for b in conf.boxes() if b.configured]
        src_id = request.query_params.get("reply")
        mode = request.query_params.get("mode") or "reply"
        if src_id and not src_id.isdigit():
            return Response({"detail": "Номер письма — числом"}, status=status.HTTP_400_BAD_REQUEST)
        out = {"boxes": [{"name": b.name, "address": b.address} for b in conf.boxes() if b.configured],
               "from": boxes[0] if boxes else "", "to": "", "cc": "", "subject": "", "text": "", "attachments": []}
        if not src_id:
            return Response(out)
        src = get_object_or_404(Message, pk=src_id)
        box = conf.box(src.box)
        out["from"] = src.box if src.box in boxes else out["from"]
        own = {b.address for b in conf.boxes()}
        subject = src.subject or ""
        if mode == "forward":
            out["subject"] = subject if subject.lower().startswith(("fwd:", "fw:")) else f"Fwd: {subject}"
            head = [
                "", "", "---------- Пересылаемое письмо ----------",
                f"От: {src.from_name + ' <' + src.from_addr + '>' if src.from_name else src.from_addr}",
                f"Кому: {src.to_addrs}", f"Тема: {subject}", "",
            ]
            out["text"] = "\n".join(head) + (src.text or "")
            out["attachments"] = [a for a in src.attachments if not a.get("inline")]
            return Response(out)
        out["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if src.direction == Direction.OUT:
            out["to"] = src.to_addrs
        else:
            out["to"] = src.reply_to or src.from_addr
        if mode == "all":
            others = [a for _, a in getaddresses([src.to_addrs, src.cc_addrs])
                      if a and a.lower() not in own and a.lower() != out["to"].lower()]
            out["cc"] = ", ".join(dict.fromkeys(others))
        out["text"] = "\n\n" + quote_text(src)
        if box is None:
            out["from"] = out["from"] or ""
        return Response(out)


class SendView(APIView):
    """POST multipart: from, to, cc, subject, text, files[], reply (id), forward (id)."""

    permission_classes = [CanMail]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        d = request.data
        try:
            reply_id = int(d["reply"]) if d.get("reply") else None
            forward_id = int(d["forward"]) if d.get("forward") else None
        except (TypeError, ValueError):
            return Response({"detail": "Номер письма — числом"}, status=status.HTTP_400_BAD_REQUEST)
        reply = get_object_or_404(Message, pk=reply_id) if reply_id else None
        forward = get_object_or_404(Message, pk=forward_id) if forward_id else None
        try:
            msg = send(
                box_name=d.get("from", ""), user=request.user, to=d.get("to", ""), cc=d.get("cc", ""),
                subject=d.get("subject", ""), text=d.get("text", ""),
                uploads=request.FILES.getlist("files") if hasattr(request, "FILES") else [],
                reply_to=reply, forward=forward,
            )
        except SendError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(detail(msg), status=status.HTTP_201_CREATED)


class DnsView(APIView):
    permission_classes = [CanMail]

    def get(self, request):
        return Response({"dns": [dns.check(d) for d in dns.domains()]})
