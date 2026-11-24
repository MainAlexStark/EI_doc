"""Находки независимого ревью «Почты» — по тесту на пункт."""

from __future__ import annotations

import datetime as dt
import imaplib
import os
import smtplib
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Role, User
from apps.mailbox import actions, conf, parse
from apps.mailbox.imap import Client, ImapError
from apps.mailbox.lock import BoxLock
from apps.mailbox.models import Folder, FolderState, Message
from apps.mailbox.sync import sync_all
from apps.mailbox.tests.fakeimap import FakeIMAP
from apps.mailbox.tests.test_mailbox import ENV, SENT_UTF7, Base, mime
from apps.notify.models import NotifyEvent, OutboxMail, OutboxStatus


def related_with_ctype(ctype: str) -> bytes:
    return (
        b"From: a@b.ru\r\nTo: info@eicounters.ru\r\nSubject: x\r\nMessage-ID: <cid@x>\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/related; boundary="B"\r\n\r\n--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n'
        b'<p>cid:logo</p><img src="cid:logo">\r\n--B\r\n'
        b"Content-Type: " + ctype.encode() + b"\r\nContent-ID: <logo>\r\nContent-Transfer-Encoding: base64\r\n\r\n"
        b"iVBORw0KGgo=\r\n--B--\r\n"
    )


# 1 -------------------------------------------------------------------------
class InlineCidInjectionTests(TestCase):
    def test_hostile_content_type_cannot_inject_markup(self):
        raw = related_with_ctype('image/x"><meta http-equiv=refresh content="0;url=https://evil.ru">')
        p = parse.parse(raw)
        out = parse.inline_cids(raw, p.html)
        assert "<meta" not in out.lower() and "refresh" not in out.lower() and "evil" not in out
        assert "data:image/x" not in out

    def test_whitelisted_png_inlined_only_in_src(self):
        raw = related_with_ctype("image/png")
        out = parse.inline_cids(raw, parse.parse(raw).html)
        assert 'src="data:image/png;base64,' in out
        assert "<p>cid:logo</p>" in out  # текст «cid:» не трогаем

    def test_data_uri_only_for_img_src(self):
        clean = parse.sanitize('<a href="data:image/png;base64,AA">x</a><img src="data:image/svg+xml;base64,AA">')
        assert "data:" not in clean


# 3 -------------------------------------------------------------------------
class SyncLockAndBatchTests(Base):
    def test_busy_box_is_skipped_and_foreign_lock_not_released(self):
        foreign = BoxLock("info")
        assert foreign.acquire()
        with mock.patch("apps.mailbox.sync.Client", side_effect=lambda box: Client(box, conn=self.imap)):
            assert sync_all() == {"info": "уже синхронизируется"}
        mine = BoxLock("info")
        mine.release()  # чужой замок не снимается
        assert not mine.acquire()
        foreign.release()
        assert mine.acquire()
        mine.release()

    def test_batches_save_progress_and_initial_load_is_silent(self):
        for i in range(5):
            self.imap.deliver("INBOX", mime(msgid=f"<b{i}@x>"))
        User.objects.create_user(email="h@ei.test", password="x", role=Role.HEAD)
        with mock.patch.dict(os.environ, {"MAILBOX_SYNC_BATCH": "2"}):
            self.sync()
            st = FolderState.objects.get(folder=Folder.INBOX)
            assert Message.objects.filter(folder=Folder.INBOX).count() == 2 and st.last_uid == 2 and not st.initial_done
            self.sync()
            self.sync()
            st.refresh_from_db()
            assert Message.objects.filter(folder=Folder.INBOX).count() == 5 and st.initial_done
            assert NotifyEvent.objects.count() == 0  # старая почта первой загрузки — без уведомлений
            self.imap.deliver("INBOX", mime(msgid="<fresh@x>"))
            with self.captureOnCommitCallbacks(execute=True):
                assert self.sync() == 1

    def test_integrity_error_skips_message_not_whole_sync(self):
        from django.db import IntegrityError

        self.imap.deliver("INBOX", mime(msgid="<a@x>"))
        self.imap.deliver("INBOX", mime(msgid="<b@x>"))
        real = __import__("apps.mailbox.sync", fromlist=["save_message"]).save_message
        calls = []

        def flaky(**kw):
            calls.append(kw["uid"])
            if kw["uid"] == 1:
                raise IntegrityError("mailbox_uid_unique")
            return real(**kw)

        with mock.patch("apps.mailbox.sync.save_message", side_effect=flaky):
            self.sync()
        assert list(Message.objects.values_list("message_id", flat=True)) == ["<b@x>"]
        assert FolderState.objects.get(folder=Folder.INBOX).last_uid == 2

    def test_sync_button_without_broker_runs_in_background_not_in_request(self):
        user = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        c = APIClient()
        c.force_authenticate(user)
        started = []
        with mock.patch("apps.mailbox.tasks.sync_mailboxes.delay", side_effect=OSError("no broker")), \
                mock.patch("apps.mailbox.api.threading.Thread") as thread, \
                mock.patch("apps.mailbox.sync.sync_all", side_effect=AssertionError("в запросе синхронизировать нельзя")):
            thread.return_value.start.side_effect = lambda: started.append(True)
            r = c.post("/api/mail/sync/")
        assert r.status_code == 202 and started

    def test_tasks_go_to_mail_queue(self):
        from apps.mailbox import tasks

        assert tasks.sync_mailboxes.queue == tasks.deliver_mailbox_outbox.queue == tasks.push_flag.queue == "mail"


# 4, 5 ----------------------------------------------------------------------
class ImapErrorTests(Base):
    def test_abort_and_oserror_become_imap_error(self):
        for exc in (imaplib.IMAP4.abort("socket closed"), OSError("reset"), TimeoutError("t")):
            self.imap.uid = mock.Mock(side_effect=exc)
            with self.assertRaises(ImapError), Client(self.box, conn=self.imap) as c:
                c.select("INBOX")
                c.uids()

    def test_one_broken_box_does_not_stop_others(self):
        env = {**ENV, "MAILBOXES": "bad=bad@eicounters.ru,info=info@eicounters.ru", "MAILBOX_PASSWORD_BAD": "secret"}
        self.imap.deliver("INBOX", mime(msgid="<ok@x>"))

        def factory(box):
            if box.name == "bad":
                broken = FakeIMAP()
                broken.list = mock.Mock(side_effect=imaplib.IMAP4.abort("EOF"))
                return Client(box, conn=broken)
            return Client(box, conn=self.imap)

        with mock.patch.dict(os.environ, env), mock.patch("apps.mailbox.sync.Client", side_effect=factory):
            out = sync_all()
        assert out["info"].startswith("новых") and "EOF" in out["bad"]
        assert "EOF" in FolderState.objects.get(box="bad", folder=Folder.INBOX).error
        assert Message.objects.filter(box="info").exists()

    def test_action_network_error_is_action_error(self):
        self.imap.deliver("INBOX", mime(msgid="<a@x>"))
        self.sync()
        msg = Message.objects.get()
        self.imap.uid = mock.Mock(side_effect=OSError("broken pipe"))
        with self.assertRaises(actions.ActionError):
            actions.apply(msg, "archive", client_factory=lambda box: Client(box, conn=self.imap))

    def test_failed_login_closes_socket(self):
        self.imap.password = "other"
        self.imap.shutdown = mock.Mock()
        with self.assertRaises(ImapError):
            with Client(self.box, conn=self.imap):
                pass
        self.imap.shutdown.assert_called_once()


# 6 -------------------------------------------------------------------------
class OpenWithoutImapTests(Base):
    def test_get_does_not_touch_imap_and_seen_reaches_server_on_next_sync(self):
        uid = self.imap.deliver("INBOX", mime(msgid="<a@x>"))
        self.sync()
        msg = Message.objects.get()
        user = User.objects.create_user(email="o@ei.test", password="x", role=Role.OWNER)
        c = APIClient()
        c.force_authenticate(user)
        with mock.patch("apps.mailbox.actions.Client", side_effect=AssertionError("GET не ходит в IMAP")), \
                mock.patch("apps.mailbox.tasks.push_flag.delay", side_effect=OSError("no broker")), \
                self.captureOnCommitCallbacks(execute=True):
            assert c.get(f"/api/mail/messages/{msg.pk}/").status_code == 200
        msg.refresh_from_db()
        assert msg.seen and msg.flags_dirty
        self.sync()  # флаг сильнее серверного — уходит на сервер, а не затирается
        msg.refresh_from_db()
        assert msg.seen and not msg.flags_dirty
        assert "\\seen" in self.imap.folders["INBOX"]["msgs"][uid]["flags"]


# 7 -------------------------------------------------------------------------
class FileCleanupTests(Base):
    def test_delete_from_trash_removes_eml(self):
        self.imap.deliver("Trash", mime(msgid="<t@x>"))
        self.sync()
        msg = Message.objects.get()
        path = msg.raw.path
        with self.captureOnCommitCallbacks(execute=True):
            actions.apply(msg, "delete", client_factory=lambda box: Client(box, conn=self.imap))
        assert not os.path.exists(path)


# 8 -------------------------------------------------------------------------
class BatchingTests(Base):
    def test_digest_lines_without_n_plus_one(self):
        from apps.notify import events

        head = User.objects.create_user(email="h@ei.test", password="x", role=Role.HEAD)
        self.sync()
        for i in range(5):
            self.imap.deliver("INBOX", mime(msgid=f"<n{i}@x>"))
        with self.captureOnCommitCallbacks(execute=False):
            self.sync()
        group = list(NotifyEvent.objects.filter(user=head).select_related("user"))
        assert len(group) == 5
        with self.assertNumQueries(1):
            lines = events._lines(group)
        assert len(lines) == 5

    def test_one_flush_kick_for_many_new_messages(self):
        User.objects.create_user(email="h@ei.test", password="x", role=Role.HEAD)
        self.sync()
        for i in range(3):
            self.imap.deliver("INBOX", mime(msgid=f"<k{i}@x>"))
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.sync()
        from apps.notify.events import kick

        assert sum(1 for cb in callbacks if cb is kick) == 1


# 9 -------------------------------------------------------------------------
class SendStateTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="b@ei.test", password="x", role=Role.HEAD)

    def test_unknown_outcome_is_failed_not_retried(self):
        from apps.mailbox.send import send
        from apps.notify.outbox import deliver_mailbox

        msg = send(box_name="info", user=self.user, to="a@b.ru", cc="", subject="s", text="t")
        with mock.patch("apps.mailbox.send.smtp_send", side_effect=__import__("apps.mailbox.send", fromlist=["x"]).MaybeSent("SMTPServerDisconnected")):
            assert deliver_mailbox() == 0
        out = OutboxMail.objects.get(pk=msg.outbox_id)
        assert out.status == OutboxStatus.FAILED and "могло уйти" in out.last_error
        assert deliver_mailbox() is None  # повторять нечего

    def test_disconnect_during_data_is_maybe_sent(self):
        from apps.mailbox.send import MaybeSent, smtp_send

        server = mock.Mock()
        server.sendmail.side_effect = smtplib.SMTPServerDisconnected("closed")
        with mock.patch("apps.mailbox.send.smtplib.SMTP_SSL", return_value=server), self.assertRaises(MaybeSent):
            smtp_send(self.box, b"raw", ["a@b.ru"])

    def test_stale_sending_becomes_failed(self):
        from django.utils import timezone

        from apps.notify.outbox import fail_stale_sending

        out = OutboxMail.objects.create(to="a@b.ru", subject="s", text="", template="mailbox", sender="info",
                                        status=OutboxStatus.SENDING,
                                        next_attempt_at=timezone.now() - dt.timedelta(hours=1))
        assert fail_stale_sending() == 1
        out.refresh_from_db()
        assert out.status == OutboxStatus.FAILED and "могло уйти" in out.last_error

    def test_outgoing_always_has_message_id(self):
        from apps.mailbox.send import send

        msg = send(box_name="info", user=self.user, to="a@b.ru", cc="", subject="s", text="t")
        assert msg.message_id.startswith("<") and msg.message_id.endswith("@eicounters.ru>")


# 10 ------------------------------------------------------------------------
class ExternalMoveTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.imap.add_folder("Archive")
        User.objects.create_user(email="h@ei.test", password="x", role=Role.HEAD)
        self.sync()

    def _move(self, src: str, dst: str) -> None:
        msgs = self.imap.folders[src]["msgs"]
        uid, m = next(iter(msgs.items()))
        del msgs[uid]
        self.imap.deliver(dst, m["raw"], m["flags"])

    def test_archive_and_back_keeps_one_row_and_no_notification(self):
        self.imap.deliver("INBOX", mime(msgid="<mv@x>"))
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        assert NotifyEvent.objects.count() == 1
        pk = Message.objects.get().pk
        self._move("INBOX", "Archive")
        self.sync()
        self._move("Archive", "INBOX")
        with self.captureOnCommitCallbacks(execute=True):
            self.sync()
        rows = list(Message.objects.filter(uid__isnull=False).values_list("pk", "folder"))
        assert rows == [(pk, Folder.INBOX)]  # та же строка: переписка и файл целы
        assert NotifyEvent.objects.count() == 1  # возврат из архива — не «новое письмо»

    def test_message_without_message_id_matched_by_fingerprint(self):
        raw = b"From: a@b.ru\r\nTo: info@eicounters.ru\r\nSubject: no id\r\nDate: Fri, 17 Jul 2026 10:00:00 +0300\r\n\r\nhi\r\n"
        self.imap.deliver("INBOX", raw)
        self.sync()
        pk = Message.objects.get().pk
        self._move("INBOX", "Archive")
        self.sync()
        assert list(Message.objects.values_list("pk", "folder")) == [(pk, Folder.ARCHIVE)]


# 11 ------------------------------------------------------------------------
class NoUidplusTests(Base):
    def test_delete_without_uidplus_only_marks(self):
        self.imap.capabilities = ("IMAP4REV1",)
        uid = self.imap.deliver("Trash", mime(msgid="<d@x>"))
        self.imap.deliver("Trash", mime(msgid="<other@x>"), flags={"\\deleted"})  # чужая пометка
        self.sync()
        msg = Message.objects.get(message_id="<d@x>")
        actions.apply(msg, "delete", client_factory=lambda box: Client(box, conn=self.imap))
        trash = self.imap.folders["Trash"]["msgs"]
        assert uid in trash and "\\deleted" in trash[uid]["flags"] and len(trash) == 2


# 12 ------------------------------------------------------------------------
class BadInputTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        self.c = APIClient()
        self.c.force_authenticate(self.user)

    def test_crlf_in_attachment_name(self):
        f = SimpleUploadedFile("акт\r\nBcc: evil@x.ru.pdf", b"%PDF", content_type="application/pdf")
        r = self.c.post("/api/mail/send/", {"from": "info", "to": "a@b.ru", "subject": "s", "text": "t", "files": [f]},
                        format="multipart")
        assert r.status_code == 201, r.data
        from apps.mailbox.store import read_raw

        raw = read_raw(Message.objects.get(pk=r.data["id"]))
        assert b"\r\nBcc:" not in raw

    def test_non_numeric_ids_are_400(self):
        r = self.c.post("/api/mail/send/", {"from": "info", "to": "a@b.ru", "text": "t", "reply": "abc"}, format="json")
        assert r.status_code == 400
        assert self.c.get("/api/mail/compose/?reply=abc").status_code == 400

    def test_assistant_bad_numbers(self):
        from apps.assistant.tools import ToolContext, ToolFailure
        from apps.mailbox.assistant_tools import mailbox_read, mailbox_search

        ctx = ToolContext(user=self.user, conversation=None)
        with self.assertRaises(ToolFailure):
            mailbox_search(ctx, {"limit": "много"})
        with self.assertRaises(ToolFailure):
            mailbox_read(ctx, {"id": "abc"})


# 13, 14 --------------------------------------------------------------------
class SmallTests(TestCase):
    def test_remote_images_variants(self):
        for html in ('<IMG SRC="https://t.er/p.gif">', '<img src="//t.er/p.gif">',
                     '<div style="background:URL(//t.er/p.gif)">x</div>', "<div style=\"background:url('http://t.er/x')\">x</div>"):
            assert parse.has_remote_images(parse.sanitize(html)), html
        assert not parse.has_remote_images(parse.sanitize('<img src="data:image/png;base64,AA">'))

    def test_password_not_in_repr(self):
        with mock.patch.dict(os.environ, ENV):
            assert "secret" not in repr(conf.box("info"))
