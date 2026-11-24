"""Почта фирмы: разбор MIME, очистка HTML, синхронизация по UID, действия, отправка, права, уведомления."""

from __future__ import annotations

import base64
import datetime as dt
import os
from email.message import EmailMessage
from unittest import mock

from django.core import mail as djmail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.mailbox import actions, conf, parse
from apps.mailbox.imap import Client, parse_list_line, roles, utf7_decode, utf7_encode
from apps.mailbox.models import Direction, Folder, FolderState, Message
from apps.mailbox.send import compose, deliver_one, quote_text, send
from apps.mailbox.sync import sync_box
from apps.mailbox.tests.fakeimap import FakeIMAP
from apps.notify.models import NotifyEvent, OutboxMail, OutboxStatus

SENT_UTF7 = "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"  # «Отправленные»
ENV = {"MAILBOXES": "info=info@eicounters.ru", "MAILBOX_PASSWORD_INFO": "secret"}


def mime(subject="Поверка счётчиков", frm="Иван <ivan@client.ru>", to="info@eicounters.ru", text="Здравствуйте!",
         html=None, msgid="<m1@client.ru>", in_reply_to=None, attach=None, charset="utf-8", inline_png=False):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, to, subject, msgid
    m["Date"] = "Fri, 17 Jul 2026 10:00:00 +0300"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(text, charset=charset)
    if html:
        m.add_alternative(html, subtype="html")
        if inline_png:
            m.get_payload()[1].add_related(b"\x89PNG\r\n\x1a\nfake", maintype="image", subtype="png", cid="<logo@x>")
    if attach:
        m.add_attachment(attach[1], maintype="application", subtype="pdf", filename=attach[0])
    return m.as_bytes().replace(b"\n", b"\r\n")


class Base(TestCase):
    def setUp(self) -> None:
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.box = conf.box("info")
        self.imap = FakeIMAP()
        self.imap.add_folder(SENT_UTF7, "\\Sent")
        self.imap.add_folder("Spam", "\\Junk")
        self.imap.add_folder("Trash", "\\Trash")

    def imap_client(self, *_):
        return Client(self.box, conn=self.imap)

    def sync(self) -> int:
        return sync_box(self.box, client=self.imap_client())


# ------------------------------------------------------------------ разбор
class ParseTests(TestCase):
    def test_cyrillic_attachments_inline(self):
        raw = mime(html='<p>Привет<img src="cid:logo@x"></p>', attach=("акт приёмки.pdf", b"%PDF-1.4"),
                   inline_png=True)
        p = parse.parse(raw)
        assert p.subject == "Поверка счётчиков" and p.from_name == "Иван" and p.from_addr == "ivan@client.ru"
        assert "Здравствуйте" in p.text
        names = {a.name: a for a in p.attachments}
        assert names["акт приёмки.pdf"].type == "application/pdf" and not names["акт приёмки.pdf"].inline
        inline = [a for a in p.attachments if a.inline]
        assert inline and inline[0].cid == "logo@x"
        doc = parse.inline_cids(raw, p.html)
        assert "data:image/png;base64," in doc and "cid:" not in doc
        att, data = parse.part(raw, names["акт приёмки.pdf"].n)
        assert data == b"%PDF-1.4"

    def test_windows_1251_and_koi8(self):
        for cs in ("windows-1251", "koi8-r"):
            p = parse.parse(mime(text="Счётчик воды ДУ-15", charset=cs))
            assert "Счётчик воды" in p.text, cs

    def test_broken_charset_does_not_crash(self):
        raw = (b"From: a@b.ru\r\nSubject: =?x-unknown?B?0J/RgNC40LLQtdGC?=\r\nContent-Type: text/plain; "
               b"charset=x-unknown\r\n\r\n\xcf\xf0\xe8\xe2\xe5\xf2\r\n")
        p = parse.parse(raw)
        assert "Привет" in p.text

    def test_sanitize(self):
        dirty = ('<p onclick="x()">Hi<script>alert(1)</script><img src=x onerror="alert(2)">'
                 '<a href="javascript:alert(3)">j</a><a href="data:text/html,x">d</a><a href="https://ok.ru">ok</a>'
                 '<iframe src="https://evil"></iframe><form action="https://evil"><input></form>'
                 '<div style="background:url(javascript:alert(1))">s</div><img src="https://track.er/p.gif"></p>')
        clean = parse.sanitize(dirty)
        for bad in ("script", "onerror", "onclick", "javascript:", "data:text", "iframe", "<form", "<input"):
            assert bad not in clean.lower(), bad
        assert 'href="https://ok.ru"' in clean and 'rel="noopener noreferrer"' in clean
        assert parse.has_remote_images(clean)

    def test_safe_attachment_type(self):
        assert parse.safe_attachment_type("text/html") == "application/octet-stream"
        assert parse.safe_attachment_type("image/svg+xml") == "application/octet-stream"
        assert parse.safe_attachment_type("application/pdf") == "application/pdf"


class ImapHelpersTests(TestCase):
    def test_utf7_roundtrip(self):
        assert utf7_decode(SENT_UTF7) == "Отправленные"
        assert utf7_encode("Отправленные") == SENT_UTF7
        assert utf7_decode("A&-B") == "A&B" and utf7_encode("A&B") == "A&-B"

    def test_roles_special_use_and_names(self):
        listed = [parse_list_line(x) for x in (
            b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "' + SENT_UTF7.encode() + b'"',
            b'(\\HasNoChildren \\Junk) "/" "Spam"', b'(\\HasNoChildren) "/" "Trash"',
            b'(\\Noselect) "/" "Archive"',
        )]
        r = roles(listed)
        assert r[Folder.SENT] == SENT_UTF7 and r[Folder.JUNK] == "Spam" and r[Folder.TRASH] == "Trash"
        assert Folder.ARCHIVE not in r  # \Noselect — не папка


# ------------------------------------------------------------------ синхронизация
class SyncTests(Base):
    def test_first_load_then_incremental_without_notifying_old_mail(self):
        self.imap.deliver("INBOX", mime(msgid="<a@x>"))
        self.imap.deliver("INBOX", mime(msgid="<b@x>"), flags={"\\Seen"})
        with self.captureOnCommitCallbacks(execute=True):
            assert self.sync() == 0  # первая загрузка — не «новые письма»
        assert Message.objects.filter(folder=Folder.INBOX).count() == 2
        assert Message.objects.get(message_id="<b@x>").seen
        assert Folder.ARCHIVE in dict(FolderState.objects.values_list("folder", "imap_name"))  # Archive создан
        assert NotifyEvent.objects.count() == 0

        self.imap.deliver("INBOX", mime(msgid="<c@x>"))
        with self.captureOnCommitCallbacks(execute=True):
            assert self.sync() == 1
        assert Message.objects.filter(folder=Folder.INBOX).count() == 3

    def test_flags_and_removal_follow_server(self):
        uid = self.imap.deliver("INBOX", mime(msgid="<a@x>"))
        self.sync()
        self.imap.folders["INBOX"]["msgs"][uid]["flags"].add("\\seen")
        self.sync()
        assert Message.objects.get(message_id="<a@x>").seen
        del self.imap.folders["INBOX"]["msgs"][uid]
        self.sync()
        msg = Message.objects.get(message_id="<a@x>")  # пропало с сервера — сначала отвязано
        assert msg.uid is None and msg.detached_at is not None
        path = msg.raw.path
        Message.objects.filter(pk=msg.pk).update(detached_at=msg.detached_at - dt.timedelta(hours=1))
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        assert not Message.objects.filter(message_id="<a@x>").exists()
        assert not os.path.exists(path)  # и .eml удалён (post_delete)

    def test_uidvalidity_change_reloads_folder(self):
        self.imap.deliver("INBOX", mime(msgid="<a@x>"))
        self.sync()
        self.imap.folders["INBOX"] = {"uidvalidity": 2000, "next": 1, "msgs": {}}
        self.imap.deliver("INBOX", mime(msgid="<z@x>"))
        self.sync()
        ids = set(Message.objects.filter(folder=Folder.INBOX, uid__isnull=False).values_list("message_id", flat=True))
        assert ids == {"<z@x>"}
        assert Message.objects.get(message_id="<a@x>").detached_at is not None
        assert FolderState.objects.get(folder=Folder.INBOX).uidvalidity == 2000

    def test_threads_by_references(self):
        self.imap.deliver("INBOX", mime(msgid="<q@x>", subject="Счётчики"))
        self.sync()
        self.imap.deliver("INBOX", mime(msgid="<q2@x>", subject="Re: Счётчики", in_reply_to="<q@x>"))
        self.sync()
        a, b = Message.objects.get(message_id="<q@x>"), Message.objects.get(message_id="<q2@x>")
        assert a.thread_root_id == b.thread_root_id == a.pk

    def test_wrong_password_reported(self):
        self.imap.password = "other"
        from apps.mailbox.imap import ImapError

        with self.assertRaises(ImapError):
            self.sync()


# ------------------------------------------------------------------ действия
class ActionTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.uid = self.imap.deliver("INBOX", mime(msgid="<a@x>"))
        self.sync()
        self.msg = Message.objects.get(message_id="<a@x>")

    def test_read_and_flag_go_to_server(self):
        actions.apply(self.msg, "read", client_factory=self.imap_client)
        actions.apply(self.msg, "flag", client_factory=self.imap_client)
        assert self.imap.folders["INBOX"]["msgs"][self.uid]["flags"] >= {"\\seen", "\\flagged"}
        actions.apply(self.msg, "unread", client_factory=self.imap_client)
        assert "\\seen" not in self.imap.folders["INBOX"]["msgs"][self.uid]["flags"]

    def test_move_to_archive_and_back_no_duplicates(self):
        actions.apply(self.msg, "archive", client_factory=self.imap_client)
        assert not self.imap.folders["INBOX"]["msgs"] and self.imap.folders["Archive"]["msgs"]
        self.msg.refresh_from_db()
        assert self.msg.folder == Folder.ARCHIVE
        self.sync()
        assert Message.objects.filter(message_id="<a@x>").count() == 1
        actions.apply(Message.objects.get(message_id="<a@x>"), "inbox", client_factory=self.imap_client)
        self.sync()
        assert list(Message.objects.filter(message_id="<a@x>").values_list("folder", flat=True)) == [Folder.INBOX]

    def test_move_without_move_capability(self):
        self.imap.capabilities = ("IMAP4REV1",)  # ни MOVE, ни UIDPLUS
        actions.apply(self.msg, "spam", client_factory=self.imap_client)
        assert self.imap.folders["Spam"]["msgs"]
        # Без UIDPLUS общий EXPUNGE не делаем (стёр бы чужие пометки) — письмо лишь помечено.
        assert self.imap.folders["INBOX"]["msgs"][self.uid]["flags"] >= {"\\deleted"}

    def test_delete_only_from_trash(self):
        with self.assertRaises(actions.ActionError):
            actions.apply(self.msg, "delete", client_factory=self.imap_client)
        actions.apply(self.msg, "trash", client_factory=self.imap_client)
        self.sync()
        msg = Message.objects.get(message_id="<a@x>")
        actions.apply(msg, "delete", client_factory=self.imap_client)
        assert not Message.objects.filter(message_id="<a@x>").exists()
        assert not self.imap.folders["Trash"]["msgs"]


# ------------------------------------------------------------------ отправка
class SendTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="boss@ei.test", password="x", role=Role.HEAD)
        self.imap.deliver("INBOX", mime(msgid="<q@client.ru>", subject="Вопрос по поверке"))
        self.sync()
        self.src = Message.objects.get(message_id="<q@client.ru>")

    def test_reply_headers_queue_smtp_and_append(self):
        with self.captureOnCommitCallbacks(execute=False):
            msg = send(box_name="info", user=self.user, to=self.src.from_addr, cc="", subject="Re: Вопрос по поверке",
                       text="Ответ\n\n" + quote_text(self.src),
                       uploads=[SimpleUploadedFile("счёт.pdf", b"%PDF", content_type="application/pdf")],
                       reply_to=self.src)
        assert msg.thread_root_id == self.src.thread_root_id and msg.direction == Direction.OUT
        out = OutboxMail.objects.get(pk=msg.outbox_id)
        assert out.sender == "info" and out.status == OutboxStatus.PENDING
        with mock.patch("apps.mailbox.send.smtp_send") as smtp, \
                mock.patch("apps.mailbox.imap.Client", side_effect=lambda box: Client(box, conn=self.imap)):
            from apps.notify.outbox import deliver, deliver_mailbox

            assert deliver() == 0  # служебная очередь письма из ящиков не трогает
            assert deliver_mailbox() == 1
        raw = smtp.call_args.args[1]
        p = parse.parse(raw)
        assert p.in_reply_to == "<q@client.ru>" and "<q@client.ru>" in p.refs
        assert p.from_addr == "info@eicounters.ru" and smtp.call_args.args[2] == ["ivan@client.ru"]
        assert any(a.name == "счёт.pdf" for a in p.attachments)
        assert "пишет:" in p.text and "> Здравствуйте!" in p.text
        sent = self.imap.folders[SENT_UTF7]["msgs"]
        assert len(sent) == 1 and "\\seen" in next(iter(sent.values()))["flags"]
        out.refresh_from_db()
        assert out.status == OutboxStatus.SENT
        # Копия из «Отправленных» подвязывается к нашей строке, а не дублируется.
        self.sync()
        assert Message.objects.filter(message_id=p.message_id).count() == 1
        assert Message.objects.get(message_id=p.message_id).uid is not None

    def test_validation(self):
        from apps.mailbox.send import SendError

        for kwargs, err in (({"to": ""}, "получателя"), ({"to": "not an address"}, "адрес"),
                            ({"text": "  "}, "Пустое"), ({"box_name": "nope"}, "ящика")):
            base = {"box_name": "info", "user": self.user, "to": "a@b.ru", "cc": "", "subject": "s", "text": "t"}
            base.update(kwargs)
            with self.assertRaisesMessage(SendError, err):
                send(**base)

    def test_smtp_failure_is_visible_and_retried(self):
        msg = send(box_name="info", user=self.user, to="a@b.ru", cc="", subject="s", text="t")
        with mock.patch("apps.mailbox.send.smtp_send", side_effect=OSError("connection refused")):
            from apps.notify.outbox import deliver_mailbox

            assert deliver_mailbox() == 0
        out = OutboxMail.objects.get(pk=msg.outbox_id)
        assert out.status == OutboxStatus.PENDING and "connection refused" in out.last_error


# ------------------------------------------------------------------ API и права
class ApiTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.imap.deliver("INBOX", mime(msgid="<a@x>", html='<p>Hi<img src="https://t.er/p.gif"></p>'))
        self.sync()
        self.msg = Message.objects.get(message_id="<a@x>")

    def as_role(self, role) -> APIClient:
        user = User.objects.create_user(email=f"{role}@ei.test", password="x", role=role)
        Employee.objects.create(tab_number=role[:4], full_name="Тест Тест", user=user)
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_worker_and_observer_forbidden(self):
        for role in (Role.VERIFIER, Role.OBSERVER, Role.METROLOGIST):
            c = self.as_role(role)
            for url in ("/api/mail/status/", "/api/mail/messages/", f"/api/mail/messages/{self.msg.pk}/body/"):
                assert c.get(url).status_code == 403, (role, url)

    def test_manager_lists_reads_and_body_is_locked_down(self):
        c = self.as_role(Role.MANAGER)
        r = c.get("/api/mail/status/")
        assert r.status_code == 200 and r.data["unread"] == 1 and r.data["enabled"]
        r = c.get("/api/mail/messages/?folder=inbox&q=Поверка")
        assert [m["id"] for m in r.data["results"]] == [self.msg.pk]
        with mock.patch("apps.mailbox.actions.Client", side_effect=lambda box: Client(box, conn=self.imap)):
            r = c.get(f"/api/mail/messages/{self.msg.pk}/")
        assert r.status_code == 200 and r.data["message"]["remote_images"]
        self.msg.refresh_from_db()
        assert self.msg.seen
        body = c.get(f"/api/mail/messages/{self.msg.pk}/body/")
        doc = body.content.decode()
        assert "img-src data:;" in doc and "default-src 'none'" in doc and "sandbox" in body["Content-Security-Policy"]
        assert "img-src data: https: http:" in c.get(f"/api/mail/messages/{self.msg.pk}/body/?images=1").content.decode()

    def test_attachment_always_download(self):
        self.imap.deliver("INBOX", mime(msgid="<att@x>", attach=("evil.pdf", b"%PDF")))
        self.sync()
        m = Message.objects.get(message_id="<att@x>")
        n = m.attachments[0]["n"]
        r = self.as_role(Role.OWNER).get(f"/api/mail/messages/{m.pk}/att/{n}/")
        assert r.status_code == 200 and r["Content-Disposition"].startswith("attachment")
        assert r["X-Content-Type-Options"] == "nosniff"

    def test_compose_reply_all_excludes_own_boxes(self):
        self.imap.deliver("INBOX", mime(msgid="<all@x>", to="info@eicounters.ru, petr@client.ru"))
        self.sync()
        m = Message.objects.get(message_id="<all@x>")
        r = self.as_role(Role.HEAD).get(f"/api/mail/compose/?reply={m.pk}&mode=all")
        assert r.data["to"] == "ivan@client.ru" and r.data["cc"] == "petr@client.ru"
        assert r.data["subject"].startswith("Re: ") and "пишет:" in r.data["text"]

    def test_disabled_shows_env_hint(self):
        with mock.patch.dict(os.environ, {"MAILBOXES": ""}):
            r = self.as_role(Role.OWNER).get("/api/mail/status/")
        assert not r.data["enabled"] and any("MAILBOXES" in line for line in r.data["env_hint"])


# ------------------------------------------------------------------ уведомления
@override_settings(SMTP_HOST="smtp.test")
class NotifyTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.head = User.objects.create_user(email="head@ei.test", password="x", role=Role.HEAD)
        self.boxuser = User.objects.create_user(email="info@eicounters.ru", password="x", role=Role.MANAGER)
        self.worker = User.objects.create_user(email="w@ei.test", password="x", role=Role.VERIFIER)
        self.sync()  # первая загрузка пустых папок

    def test_new_mail_notifies_management_but_not_company_box(self):
        self.imap.deliver("INBOX", mime(msgid="<n@x>", subject="Заявка на поверку"))
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        to = [m.to[0] for m in djmail.outbox]
        assert "head@ei.test" in to
        assert "info@eicounters.ru" not in to  # петля: уведомление стало бы новым письмом
        assert "w@ei.test" not in to
        assert any("Заявка на поверку" in m.body for m in djmail.outbox)

    def test_auto_submitted_does_not_notify(self):
        raw = mime(msgid="<auto@x>").replace(b"Subject:", b"Auto-Submitted: auto-generated\r\nSubject:", 1)
        self.imap.deliver("INBOX", raw)
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        assert NotifyEvent.objects.filter(kind="mail_new").count() == 0

    def test_notification_mail_itself_is_marked_auto(self):
        from apps.notify import outbox

        outbox.enqueue("test", "head@ei.test", {"name": "x"})
        outbox.deliver()
        assert djmail.outbox[-1].extra_headers.get("Auto-Submitted") == "auto-generated"


class AssistantToolTests(Base):
    def test_search_and_read_respect_roles(self):
        from apps.assistant.tools import ToolContext, ToolFailure
        from apps.mailbox.assistant_tools import mailbox_read, mailbox_search

        self.imap.deliver("INBOX", mime(msgid="<s@x>", subject="Счёт на оплату"))
        self.sync()
        head = User.objects.create_user(email="h@ei.test", password="x", role=Role.HEAD)
        worker = User.objects.create_user(email="v@ei.test", password="x", role=Role.VERIFIER)
        res = mailbox_search(ToolContext(user=head, conversation=None), {"query": "оплату"})  # в SQLite регистр кириллицы различается; в Postgres — нет
        assert "Счёт на оплату" in res.content
        mid = Message.objects.get(message_id="<s@x>").pk
        assert "Здравствуйте" in mailbox_read(ToolContext(user=head, conversation=None), {"id": mid}).content
        with self.assertRaises(ToolFailure):
            mailbox_search(ToolContext(user=worker, conversation=None), {})
