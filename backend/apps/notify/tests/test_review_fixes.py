"""Находки независимого ревью: старые входы, письма со ссылкой, лимиты, Telegram без учётки."""

from __future__ import annotations

import datetime as dt
from unittest import mock

from django.contrib.admin.sites import site
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Employee, Role, User
from apps.notify import outbox
from apps.notify.models import OutboxMail, OutboxStatus

OLD = "Stary-parol-42"
NEW = "Novyi-parol-2026"


def legacy_refresh(user) -> RefreshToken:
    """Refresh в том виде, как его выдавала версия до метки пароля."""
    token = RefreshToken.for_user(user)
    if api_settings.REVOKE_TOKEN_CLAIM in token:
        del token[api_settings.REVOKE_TOKEN_CLAIM]
    return token


class Base(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.user = User.objects.create_user(email="ivan@ei.test", password=OLD, role=Role.VERIFIER)
        Employee.objects.create(tab_number="05", full_name="Кузнецов Иван Петрович", user=self.user)
        self.api = APIClient()

    def refresh(self, token: str):
        return self.api.post("/api/auth/token/refresh/", {"refresh": token}, format="json")

    def bearer(self, access: str) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return client


class LegacyTokenTests(Base):
    def test_legacy_session_survives_deploy(self):
        """Выкатка не разлогинивает: старый токен без метки работает, пока пароль не меняли."""
        legacy = legacy_refresh(self.user)
        assert self.bearer(str(legacy.access_token)).get("/api/notify/prefs/").status_code == 200
        r = self.refresh(str(legacy))
        assert r.status_code == 200
        assert self.bearer(r.data["access"]).get("/api/notify/prefs/").status_code == 200

    def test_reviewer_scenario_rotated_legacy_refresh_dies_on_password_change(self):
        """refresh без метки → ротация → смена пароля → повёрнутый refresh = 401."""
        legacy = legacy_refresh(self.user)
        legacy_access = str(legacy.access_token)
        rotated = self.refresh(str(legacy)).data
        self.user.refresh_from_db()
        self.user.set_password(NEW)
        self.user.save()
        assert self.refresh(rotated["refresh"]).status_code == 401
        assert self.bearer(rotated["access"]).get("/api/notify/prefs/").status_code == 401
        assert self.bearer(legacy_access).get("/api/notify/prefs/").status_code == 401

    def test_unrotated_legacy_refresh_dies_on_password_change(self):
        legacy = legacy_refresh(self.user)
        self.user.set_password(NEW)
        self.user.save()
        assert self.refresh(str(legacy)).status_code == 401

    def test_tokens_issued_after_change_work(self):
        self.user.set_password(NEW)
        self.user.save()
        r = self.api.post("/api/auth/token/", {"email": "ivan@ei.test", "password": NEW}, format="json")
        assert r.status_code == 200
        assert self.refresh(r.data["refresh"]).status_code == 200


@override_settings(SMTP_HOST="smtp.test")
class ResetMailTests(Base):
    def forgot(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.api.post("/api/auth/password/forgot/", {"email": "ivan@ei.test"}, format="json")

    def test_link_in_fragment(self):
        self.forgot()
        body = mail.outbox[-1].body
        assert "/#reset=" in body and "?reset=" not in body

    def test_per_address_limit_is_silent(self):
        for _ in range(4):
            r = self.forgot()
            assert r.status_code == 200
        assert len([m for m in mail.outbox if "reset=" in m.body]) == 3

    def test_body_wiped_after_send_and_hidden_in_admin(self):
        self.forgot()
        row = OutboxMail.objects.get(template="password_reset")
        assert row.status == OutboxStatus.SENT
        assert "reset=" not in row.text and "reset=" not in row.html
        admin = site._registry[OutboxMail]
        assert "reset=" not in str(admin.text_shown(row)) and "скрыто" in admin.text_shown(row)

    def test_expired_link_is_not_sent(self):
        row = OutboxMail.objects.create(
            to="ivan@ei.test", subject="Сброс пароля", text="https://x/#reset=abc", template="password_reset",
            next_attempt_at=timezone.now(),
        )
        OutboxMail.objects.filter(pk=row.pk).update(created_at=timezone.now() - dt.timedelta(hours=2))
        before = len(mail.outbox)
        outbox.deliver()
        row.refresh_from_db()
        assert row.status == OutboxStatus.FAILED
        assert "reset=" not in row.text
        assert len(mail.outbox) == before

    def test_check_and_reset_have_own_throttle(self):
        """Проверка ссылки не съедает лимит «забыли пароль»."""
        for _ in range(6):
            self.api.post("/api/auth/password/reset/check/", {"token": "1.bad"}, format="json")
        assert self.forgot().status_code == 200


class TelegramUnlinkedTests(TestCase):
    def test_employee_without_account_gets_message_directly(self):
        from apps.notify.signals import _telegram_unlinked

        employee = Employee.objects.create(tab_number="07", full_name="Без Учётки", telegram_chat_id="12345")
        with mock.patch("apps.notify.events._send_telegram") as send:
            with self.captureOnCommitCallbacks(execute=True):
                _telegram_unlinked(employee, "Вам назначен выезд")
        send.assert_called_once_with("12345", "Вам назначен выезд")

    def test_employee_with_account_goes_through_digest(self):
        from apps.notify.signals import _telegram_unlinked

        user = User.objects.create_user(email="p@ei.test", password=OLD, role=Role.VERIFIER)
        employee = Employee.objects.create(tab_number="08", full_name="С Учёткой", telegram_chat_id="1", user=user)
        with mock.patch("apps.notify.events._send_telegram") as send:
            with self.captureOnCommitCallbacks(execute=True):
                _telegram_unlinked(employee, "x")
        send.assert_not_called()
