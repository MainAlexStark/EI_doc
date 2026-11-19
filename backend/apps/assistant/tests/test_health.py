"""Ассистент не работает — человек узнаёт об этом сразу и понятными словами, а не «Ошибкой 500»."""

from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.assistant import health
from apps.assistant.models import Conversation, Message, Run
from apps.core.models import Role, User


class HealthTests(TestCase):
    def setUp(self) -> None:
        health.forget()
        self.addCleanup(health.forget)
        self.user = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.conversation = Conversation.objects.create(user=self.user, model="openrouter:test/model")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def send(self):
        return self.client.post(reverse("assistant_send", args=[self.conversation.pk]), {"content": "привет"},
                                format="json")

    @override_settings(ASSISTANT_RUN_MODE="celery", CELERY_BROKER_URL="redis://nowhere.invalid:6379/0")
    def test_redis_down_is_503_with_reason_and_nothing_saved(self):
        response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertIn("Redis", response.json()["detail"])
        self.assertFalse(Message.objects.filter(conversation=self.conversation).exists())
        self.assertFalse(Run.objects.exists())

    @override_settings(ASSISTANT_RUN_MODE="celery")
    def test_no_worker_is_503(self):
        with mock.patch.object(health, "_broker_problem", return_value=""), \
                mock.patch.object(health, "_worker_problem", return_value="Не запущен воркер очереди «assistant»"):
            response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertIn("воркер", response.json()["detail"])

    @override_settings(ASSISTANT_RUN_MODE="celery")
    def test_enqueue_failure_marks_run_error_instead_of_500(self):
        with mock.patch.object(health, "queue_problems", return_value=""), \
                mock.patch("apps.assistant.tasks.run_agent.delay", side_effect=ConnectionError("refused")), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.send()
        self.assertEqual(response.status_code, 201)
        run = Run.objects.get()
        self.assertEqual(run.status, Run.Status.ERROR)
        self.assertIn("не работает", run.error)

    @override_settings(ASSISTANT_RUN_MODE="celery", CELERY_BROKER_URL="redis://nowhere.invalid:6379/0")
    def test_status_endpoint_lists_problems(self):
        data = self.client.get(reverse("assistant_status")).json()
        self.assertFalse(data["ok"])
        self.assertTrue(any("Redis" in p for p in data["problems"]))

    def test_status_ok_in_thread_mode(self):
        with override_settings(ASSISTANT_RUN_MODE="thread"), \
                mock.patch("apps.assistant.llm.default_model", return_value="openrouter:x"):
            data = self.client.get(reverse("assistant_status")).json()
        self.assertEqual(data, {"ok": True, "problems": []})


class UnhandledErrorTests(TestCase):
    def test_unexpected_exception_becomes_readable_json(self):
        user = User.objects.create_user("x@ei.test", "pw", role=Role.VERIFIER)
        client = APIClient()
        client.force_authenticate(user)
        client.raise_request_exception = False
        with mock.patch("apps.assistant.api.ConversationListView.get", side_effect=RuntimeError("boom")):
            response = client.get(reverse("assistant_conversations"))
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertIn("Внутренняя ошибка сервера", body["detail"])
        self.assertIn(body["code"], body["detail"])
