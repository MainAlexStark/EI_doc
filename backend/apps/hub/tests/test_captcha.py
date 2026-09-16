"""Капча Yandex SmartCaptcha на форме заявки — apps.hub.captcha + RequestCreateView."""

from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.hub import captcha
from apps.hub.models import Request, RequestStatus


class CaptchaModuleTestCase(TestCase):
    @override_settings(CAPTCHA_CLIENT_KEY="", CAPTCHA_SERVER_KEY="")
    def test_not_configured_without_both_keys(self):
        assert captcha.is_configured() is False

    @override_settings(CAPTCHA_CLIENT_KEY="client", CAPTCHA_SERVER_KEY="")
    def test_not_configured_with_only_client_key(self):
        assert captcha.is_configured() is False

    @override_settings(CAPTCHA_CLIENT_KEY="client", CAPTCHA_SERVER_KEY="server")
    def test_configured_with_both_keys(self):
        assert captcha.is_configured() is True
        assert captcha.client_key() == "client"

    def test_empty_token_is_never_valid(self):
        assert captcha.verify("") is False

    @override_settings(CAPTCHA_SERVER_KEY="server")
    @mock.patch("apps.hub.captcha.requests.post")
    def test_ok_status_from_yandex_is_valid(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"status": "ok"})
        assert captcha.verify("sometoken") is True

    @override_settings(CAPTCHA_SERVER_KEY="server")
    @mock.patch("apps.hub.captcha.requests.post")
    def test_failed_status_from_yandex_is_invalid(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"status": "failed"})
        assert captcha.verify("sometoken") is False

    @override_settings(CAPTCHA_SERVER_KEY="server")
    @mock.patch("apps.hub.captcha.requests.post", side_effect=captcha.requests.RequestException)
    def test_network_failure_is_treated_as_invalid(self, post):
        assert captcha.verify("sometoken") is False


class CaptchaConfigViewTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()

    @override_settings(CAPTCHA_CLIENT_KEY="", CAPTCHA_SERVER_KEY="")
    def test_reports_unconfigured_without_leaking_a_key(self):
        response = self.api.get(reverse("captcha_config"))
        assert response.status_code == 200
        assert response.data == {"configured": False, "client_key": ""}

    @override_settings(CAPTCHA_CLIENT_KEY="public-key", CAPTCHA_SERVER_KEY="secret-key")
    def test_exposes_only_the_public_client_key(self):
        response = self.api.get(reverse("captcha_config"))
        assert response.data == {"configured": True, "client_key": "public-key"}
        assert "secret-key" not in str(response.data)


@override_settings(CAPTCHA_CLIENT_KEY="client", CAPTCHA_SERVER_KEY="server")
class RequestCreateWithCaptchaTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()
        # См. комментарий в test_api.py.RequestCreateTestCase.setUp — тот же
        # общий на весь прогон AnonRateThrottle.
        cache.clear()

    def payload(self, **overrides):
        data = {
            "contact_name": "Иванов И. И.",
            "contact_phone": "+7 (999) 000-00-00",
            "address": "г. Киров, ул. Ленина, 1",
            "si_description": "Счётчик воды",
            "consent_given": True,
        }
        data.update(overrides)
        return data

    @mock.patch("apps.hub.api.captcha.verify", return_value=True)
    def test_valid_token_lets_the_request_through(self, verify):
        response = self.api.post(reverse("request_create"), self.payload(captcha_token="good"), format="json")
        assert response.status_code == 201
        verify.assert_called_once()
        assert verify.call_args.args[0] == "good"

    @mock.patch("apps.hub.api.captcha.verify", return_value=False)
    def test_invalid_token_is_rejected_without_saving_a_request(self, verify):
        count_before = Request.objects.count()
        response = self.api.post(reverse("request_create"), self.payload(captcha_token="bad"), format="json")
        assert response.status_code == 400
        assert Request.objects.count() == count_before

    @mock.patch("apps.hub.api.captcha.verify", return_value=False)
    def test_missing_token_is_rejected_when_captcha_is_configured(self, verify):
        response = self.api.post(reverse("request_create"), self.payload(), format="json")
        assert response.status_code == 400

    @mock.patch("apps.hub.api.captcha.verify")
    def test_honeypot_tripped_requests_skip_captcha_and_are_still_saved_as_spam(self, verify):
        response = self.api.post(reverse("request_create"), self.payload(website="http://spam"), format="json")
        assert response.status_code == 201
        verify.assert_not_called()
        obj = Request.objects.get(pk=response.data["id"])
        assert obj.status == RequestStatus.SPAM


class RequestCreateWithoutCaptchaConfiguredTestCase(TestCase):
    """Ключи не заданы — форма работает как раньше, без токена и без проверки."""

    def setUp(self) -> None:
        self.api = APIClient()
        cache.clear()

    @override_settings(CAPTCHA_CLIENT_KEY="", CAPTCHA_SERVER_KEY="")
    def test_request_without_a_token_still_succeeds(self):
        response = self.api.post(
            reverse("request_create"),
            {
                "contact_name": "Иванов И. И.",
                "contact_phone": "+7 (999) 000-00-00",
                "address": "г. Киров, ул. Ленина, 1",
                "si_description": "Счётчик воды",
                "consent_given": True,
            },
            format="json",
        )
        assert response.status_code == 201
