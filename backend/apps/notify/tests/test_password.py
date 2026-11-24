"""Смена пароля, «Забыли пароль?», сброс руководителем — как в openseller."""

from __future__ import annotations

import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User

OLD = "Stary-parol-42"
NEW = "Novyi-parol-2026"


class PasswordBase(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.user = User.objects.create_user(email="ivan@ei.test", password=OLD, role=Role.VERIFIER)
        self.employee = Employee.objects.create(tab_number="05", full_name="Кузнецов Иван Петрович", user=self.user)
        self.api = APIClient()

    def login(self, password: str = OLD) -> dict:
        r = self.api.post("/api/auth/token/", {"email": "ivan@ei.test", "password": password}, format="json")
        assert r.status_code == 200, r.data
        return r.data

    def authed(self, access: str) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return client


class ChangePasswordTests(PasswordBase):
    def change(self, client, **body):
        payload = {"current": OLD, "password": NEW, "password2": NEW, **body}
        with self.captureOnCommitCallbacks(execute=True):
            return client.post("/api/auth/password/change/", payload, format="json")

    def test_change_closes_other_sessions_and_keeps_this_one(self):
        other = self.login()           # телефон
        this = self.login()            # компьютер
        r = self.change(self.authed(this["access"]))
        assert r.status_code == 200, r.data
        # Старые входы больше не работают — ни access, ни refresh (даже после ротации).
        assert self.authed(other["access"]).get("/api/core/employees/me/").status_code == 401
        refresh = self.api.post("/api/auth/token/refresh/", {"refresh": other["refresh"]}, format="json")
        assert refresh.status_code == 401
        # Этому устройству выдали новые токены.
        assert self.authed(r.data["access"]).get("/api/core/employees/me/").status_code == 200
        assert self.api.post("/api/auth/token/refresh/", {"refresh": r.data["refresh"]}, format="json").status_code == 200
        # Новый пароль работает, старый — нет; пришло письмо «пароль изменён».
        self.login(NEW)
        assert self.api.post("/api/auth/token/", {"email": "ivan@ei.test", "password": OLD}, format="json").status_code == 401
        assert any("Пароль изменён" in m.subject for m in mail.outbox)

    def test_rotated_refresh_from_before_change_is_dead(self):
        old = self.login()
        rotated = self.api.post("/api/auth/token/refresh/", {"refresh": old["refresh"]}, format="json").data
        self.change(self.authed(self.login()["access"]))
        r = self.api.post("/api/auth/token/refresh/", {"refresh": rotated["refresh"]}, format="json")
        assert r.status_code == 401

    def test_wrong_current_password(self):
        r = self.change(self.authed(self.login()["access"]), current="nepravilno")
        assert r.status_code == 400 and "Текущий пароль не тот." in r.data["current"]
        self.user.refresh_from_db()
        assert self.user.check_password(OLD)

    def test_mismatch_and_weak_passwords(self):
        client = self.authed(self.login()["access"])
        assert self.change(client, password2="other-Parol-1").data["password2"] == ["Новые пароли не совпадают."]
        for weak in ("short1", "12345678", "aaaaaaaaaaaa", "ivan", "ivan@ei.test", "Ivan"):
            r = self.change(client, password=weak, password2=weak)
            assert r.status_code == 400 and r.data["password"], weak
        r = self.change(client, password=OLD, password2=OLD)
        assert r.status_code == 400

    def test_anonymous_cannot_change(self):
        r = self.api.post("/api/auth/password/change/", {"current": OLD, "password": NEW, "password2": NEW}, format="json")
        assert r.status_code == 401


@override_settings(SMTP_HOST="smtp.test")
class ForgotPasswordTests(PasswordBase):
    def forgot(self, email="ivan@ei.test"):
        with self.captureOnCommitCallbacks(execute=True):
            return self.api.post("/api/auth/password/forgot/", {"email": email}, format="json")

    def token_from_mail(self) -> str:
        body = mail.outbox[-1].body
        return re.search(r"[?#]reset=([^\s]+)", body).group(1)

    def test_reset_by_link_once(self):
        r = self.forgot()
        assert r.status_code == 200 and r.data["mail_configured"]
        assert "https://ei-doc.test/#reset=" in mail.outbox[-1].body
        token = self.token_from_mail()
        assert self.api.post("/api/auth/password/reset/check/", {"token": token}, format="json").data["ok"]
        session = self.login()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.api.post("/api/auth/password/reset/", {"token": token, "password": NEW, "password2": NEW}, format="json")
        assert r.status_code == 200, r.data
        self.login(NEW)
        assert self.authed(session["access"]).get("/api/core/employees/me/").status_code == 401
        # Второй раз ссылка не срабатывает.
        again = self.api.post("/api/auth/password/reset/", {"token": token, "password": "Esche-odin-9", "password2": "Esche-odin-9"}, format="json")
        assert again.status_code == 400 and again.data["code"] == "bad_link"
        assert not self.api.post("/api/auth/password/reset/check/", {"token": token}, format="json").data["ok"]

    def test_same_answer_for_unknown_address(self):
        known = self.forgot().data["detail"].replace("ivan@ei.test", "X")
        sent = len(mail.outbox)
        unknown = self.forgot("nobody@ei.test").data["detail"].replace("nobody@ei.test", "X")
        assert known == unknown and len(mail.outbox) == sent

    def test_garbage_token(self):
        for token in ("", "abc", "MQ.zzz-zzz", "!!.??"):
            r = self.api.post("/api/auth/password/reset/check/", {"token": token or "x"}, format="json")
            assert r.status_code == 200 and r.data["ok"] is False


class ForgotWithoutMailTests(PasswordBase):
    def test_says_mail_is_not_configured(self):
        r = self.api.post("/api/auth/password/forgot/", {"email": "ivan@ei.test"}, format="json")
        assert r.status_code == 200 and r.data["mail_configured"] is False
        assert "не настроена" in r.data["detail"] and mail.outbox == []


class AdminResetTests(PasswordBase):
    def test_temp_password_closes_sessions_and_mails_employee(self):
        boss = User.objects.create_user(email="boss@ei.test", password="x", role=Role.HEAD)
        session = self.login()
        api = APIClient()
        api.force_authenticate(boss)
        with self.captureOnCommitCallbacks(execute=True):
            r = api.post(f"/api/core/staff/{self.employee.pk}/password/", format="json")
        assert r.status_code == 200
        assert self.authed(session["access"]).get("/api/core/employees/me/").status_code == 401
        self.login(r.data["password"])
        assert any("сбросил ваш пароль" in m.subject and m.to == ["ivan@ei.test"] for m in mail.outbox)
        assert r.data["password"] not in mail.outbox[-1].body  # временный пароль письмом не шлём
