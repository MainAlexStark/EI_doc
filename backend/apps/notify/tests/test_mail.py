"""Почта: режим TLS, очередь с повторами, «почта не настроена»."""

from __future__ import annotations

import smtplib
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from apps.core.models import Role, User
from apps.notify import outbox
from apps.notify.models import OutboxMail, OutboxStatus
from apps.notify.tlsmode import implicit_tls


def test_implicit_tls_like_openseller():
    cases = [(465, "", True), (587, "", False), (1127, "", False), (1127, "implicit", True),
             (1127, " SSL ", True), (465, "starttls", False), (1126, "starttls", False)]
    for port, mode, want in cases:
        assert implicit_tls(port, mode) is want, (port, mode)


class OutboxTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="v@ei.test", password="x", role=Role.VERIFIER)

    def test_enqueue_renders_text_and_html_and_sends(self):
        with self.captureOnCommitCallbacks(execute=True):
            outbox.enqueue("test", self.user.email, {"name": "Иван"}, user=self.user)
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["v@ei.test"] and "Проверка почты" in msg.subject
        assert "Иван" in msg.body and "&" not in msg.body  # текст без HTML-экранирования
        assert msg.alternatives and "ЕИ" in msg.alternatives[0][0]
        assert OutboxMail.objects.get().status == OutboxStatus.SENT

    def test_empty_address_is_skipped(self):
        assert outbox.enqueue("test", "", {}) is None
        assert OutboxMail.objects.count() == 0

    def test_temporary_failure_is_retried_with_backoff(self):
        item = outbox.enqueue("test", self.user.email, {})  # без on_commit — сами вызовем deliver
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPServerDisconnected("x")):
            assert outbox.deliver() == 0
        item.refresh_from_db()
        assert item.status == OutboxStatus.PENDING and item.attempts == 1 and item.next_attempt_at
        assert "SMTPServerDisconnected" in item.last_error

    def test_refused_recipient_is_not_retried(self):
        item = outbox.enqueue("test", self.user.email, {})
        refused = smtplib.SMTPRecipientsRefused({"v@ei.test": (550, b"no such user")})
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=refused):
            outbox.deliver()
        item.refresh_from_db()
        assert item.status == OutboxStatus.FAILED

    def test_gives_up_after_max_attempts(self):
        item = outbox.enqueue("test", self.user.email, {})
        OutboxMail.objects.filter(pk=item.pk).update(attempts=outbox.MAX_ATTEMPTS - 1)
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("down")):
            outbox.deliver()
        item.refresh_from_db()
        assert item.status == OutboxStatus.FAILED

    @override_settings(EMAIL_BACKEND="apps.notify.backends.LogBackend")
    def test_without_smtp_letters_go_to_log_and_nothing_breaks(self):
        with self.assertLogs("apps.notify.mail", level="INFO") as logs, self.captureOnCommitCallbacks(execute=True):
            outbox.enqueue("test", self.user.email, {})
        assert any("Почта не настроена" in line for line in logs.output)
        assert OutboxMail.objects.get().status == OutboxStatus.SENT
        assert not outbox.mail_configured()
