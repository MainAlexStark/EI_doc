"""Онбординг Telegram-бота: код привязки, вебхук, отправка уведомлений."""

from __future__ import annotations

import datetime as dt
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import telegram
from apps.core.models import Employee, Role, User


class TelegramLinkCodeViewTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()
        user = User.objects.create_user(email="v@ei.test", password="pass12345", role=Role.VERIFIER)
        self.employee = Employee.objects.create(tab_number="40", full_name="Кодовый К. К.", user=user)
        self.api.force_authenticate(user)

    def test_generates_and_stores_a_code(self):
        response = self.api.post(reverse("telegram_link_code"))
        assert response.status_code == 200
        self.employee.refresh_from_db()
        assert response.data["code"] == self.employee.telegram_link_code
        assert len(response.data["code"]) == 8

    def test_requires_an_employee_record(self):
        user = User.objects.create_user(email="no-employee@ei.test", password="pass12345", role=Role.OBSERVER)
        api = APIClient()
        api.force_authenticate(user)
        response = api.post(reverse("telegram_link_code"))
        assert response.status_code == 404


@override_settings(TELEGRAM_WEBHOOK_SECRET="whsecret")
class TelegramWebhookViewTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()
        self.employee = Employee.objects.create(tab_number="41", full_name="Вебхуков В. В.")
        self.code = self.employee.generate_telegram_link_code()

    def test_wrong_secret_is_rejected_without_touching_the_employee(self):
        response = self.api.post(
            reverse("telegram_webhook", args=["wrong"]),
            {"message": {"chat": {"id": 1}, "text": f"/start {self.code}"}},
            format="json",
        )
        assert response.status_code == 200
        assert response.data == {"ok": False}
        self.employee.refresh_from_db()
        assert self.employee.telegram_chat_id == ""

    def test_right_secret_with_a_valid_code_links_the_chat(self):
        response = self.api.post(
            reverse("telegram_webhook", args=["whsecret"]),
            {"message": {"chat": {"id": 777}, "text": f"/start {self.code}"}},
            format="json",
        )
        assert response.status_code == 200
        self.employee.refresh_from_db()
        assert self.employee.telegram_chat_id == "777"
        assert self.employee.telegram_link_code == ""

    def test_expired_code_is_not_linked(self):
        self.employee.telegram_link_code_expires_at = timezone.now() - dt.timedelta(minutes=1)
        self.employee.save()
        self.api.post(
            reverse("telegram_webhook", args=["whsecret"]),
            {"message": {"chat": {"id": 777}, "text": f"/start {self.code}"}},
            format="json",
        )
        self.employee.refresh_from_db()
        assert self.employee.telegram_chat_id == ""


class SendMessageTestCase(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_without_a_token_it_only_logs_and_returns_false(self):
        assert telegram.send_message("123", "hi") is False

    @override_settings(TELEGRAM_BOT_TOKEN="token")
    @mock.patch("apps.core.telegram.requests.post")
    def test_with_a_token_it_posts_to_the_bot_api(self, post):
        post.return_value = mock.Mock(status_code=200)
        assert telegram.send_message("123", "hi") is True
        post.assert_called_once()

    @override_settings(TELEGRAM_BOT_TOKEN="token")
    @mock.patch("apps.core.telegram.requests.post", side_effect=telegram.requests.RequestException)
    def test_network_failure_is_swallowed(self, post):
        assert telegram.send_message("123", "hi") is False
