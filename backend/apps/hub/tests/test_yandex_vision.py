"""Распознавание бланка — apps.hub.yandex_vision.

Без ключей выключено, как DaData/капча (см. test_captcha.py — тот же приём).
Сетевой вызов здесь всегда мокается: юнит-тест не должен зависеть от
реального Yandex AI Studio.
"""

from __future__ import annotations

import json
from unittest import mock

from django.test import TestCase, override_settings

from apps.hub import yandex_vision as yv


class IsConfiguredTestCase(TestCase):
    @override_settings(YANDEX_VISION_API_KEY="", YANDEX_VISION_FOLDER_ID="")
    def test_not_configured_without_keys(self):
        assert yv.YandexVisionClient().is_configured is False

    @override_settings(YANDEX_VISION_API_KEY="key", YANDEX_VISION_FOLDER_ID="")
    def test_not_configured_with_only_api_key(self):
        assert yv.YandexVisionClient().is_configured is False

    @override_settings(YANDEX_VISION_API_KEY="key", YANDEX_VISION_FOLDER_ID="b1gfolder")
    def test_configured_with_both(self):
        assert yv.YandexVisionClient().is_configured is True


class RecognizeBlankTestCase(TestCase):
    def test_raises_when_not_configured(self):
        client = yv.YandexVisionClient(api_key="", folder_id="")
        with self.assertRaises(yv.RecognitionUnavailable):
            client.recognize_blank(b"fake-jpeg")

    @mock.patch("apps.hub.yandex_vision.requests.post")
    def test_parses_structured_json_from_the_completion_response(self, post):
        recognized = {
            "legible": True,
            "si_type_query": "СВК-15",
            "serial_number": "123456",
            "manufacture_year": 2022,
            "rows": [
                {"flow_rate": "0.030", "reading_start": "229.083", "reading_end": "229.089",
                 "volume_standard": "0,0063", "confidence": 0.82},
            ],
        }
        post.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": json.dumps(recognized)}}]},
        )
        client = yv.YandexVisionClient(api_key="key", folder_id="folder")

        data = client.recognize_blank(b"fake-jpeg")

        assert data == recognized
        call_kwargs = post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Api-Key key"
        assert call_kwargs["headers"]["OpenAI-Project"] == "folder"
        body = call_kwargs["json"]
        assert body["model"] == "gpt://folder/qwen3.6-35b-a3b"
        assert body["response_format"]["type"] == "json_schema"
        image_block = body["messages"][0]["content"][1]
        assert image_block["type"] == "image_url"
        assert image_block["image_url"]["url"].startswith("data:image/jpeg;base64,")

    @mock.patch("apps.hub.yandex_vision.requests.post", side_effect=yv.requests.RequestException)
    def test_network_failure_raises_recognition_unavailable(self, post):
        client = yv.YandexVisionClient(api_key="key", folder_id="folder")
        with self.assertRaises(yv.RecognitionUnavailable):
            client.recognize_blank(b"fake-jpeg")

    @mock.patch("apps.hub.yandex_vision.requests.post")
    def test_non_json_content_raises_recognition_unavailable(self, post):
        post.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "not json"}}]},
        )
        client = yv.YandexVisionClient(api_key="key", folder_id="folder")
        with self.assertRaises(yv.RecognitionUnavailable):
            client.recognize_blank(b"fake-jpeg")

    @mock.patch("apps.hub.yandex_vision.requests.post")
    def test_unexpected_response_shape_raises_recognition_unavailable(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"unexpected": True})
        client = yv.YandexVisionClient(api_key="key", folder_id="folder")
        with self.assertRaises(yv.RecognitionUnavailable):
            client.recognize_blank(b"fake-jpeg")
