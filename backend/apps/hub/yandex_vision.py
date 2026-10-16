"""Распознавание бумажного бланка — Yandex AI Studio, мультимодальная модель.

Второй сценарий офлайна: бланк печатается ``apps.verification.render.
render_blank``, фото выравнивается ``apps.verification.scan.align()``, а
здесь отдаётся модели с жёсткой JSON-схемой (см. `claude/scans.md`).

Без ключа (YANDEX_VISION_API_KEY/YANDEX_VISION_FOLDER_ID пусты) распознавание
просто выключено: is_configured() сообщает об этом заранее, поверитель
заводит поверку с тем же бланком вручную, через обычный экран «Поверки» —
отдельной ошибки на каждый запрос это не производит, как и с DaData/Yandex
SmartCaptcha (см. apps.hub.address, apps.hub.captcha, тот же приём).

ВАЖНО: модель не источник истины. Всё, что она вернёт, — черновик для
экрана сверки (apps.verification.api_scan), человек подтверждает поля,
глядя на фотографию, прежде чем поверка попадёт в наряд. Самооценка
уверенности в ответе (0..1) — не калиброванная метрика, а лишь подсказка,
что подсветить на экране.
"""

from __future__ import annotations

import base64
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

CHAT_URL = "https://ai.api.cloud.yandex.net/v1/chat/completions"

# Бланк поддерживает только короткую раскладку протокола (3 пролива —
# Qнаим/Qперех/Qнаиб, wm.LAYOUT_COMPACT). Полная раскладка — только с
# экрана, печатать под неё бланк не стали, см. claude/scans.md.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "legible": {
            "type": "boolean",
            "description": "Фото читаемо целиком: виден весь бланк, не смазано, не обрезано",
        },
        "si_type_query": {
            "type": ["string", "null"],
            "description": "Тип/модель счётчика как написано на бланке — для поиска в справочнике",
        },
        "serial_number": {"type": ["string", "null"], "description": "Заводской номер счётчика"},
        "manufacture_year": {"type": ["integer", "null"]},
        "unit_type": {
            "type": ["string", "null"],
            "enum": ["hot", "cold", None],
            "description": "Отмечен г/в (hot) или х/в (cold) галочкой",
        },
        "meter_class": {"type": ["string", "null"], "enum": ["A", "B", None]},
        "pulse_weight": {
            "type": ["string", "null"],
            "description": "Коэффициент преобразования K, если вписан",
        },
        "water_temperature": {"type": ["string", "null"]},
        "checks": {
            "type": "object",
            "properties": {
                "visual": {"type": ["boolean", "null"]},
                "operation": {"type": ["boolean", "null"]},
                "tightness": {"type": ["boolean", "null"]},
            },
        },
        "manual_unsuitable": {
            "type": ["boolean", "null"],
            "description": "Отмечена галочка «Счётчик признан непригодным» в блоке НЕПРИГОДЕН",
        },
        "manual_unsuitability_reason": {"type": ["string", "null"]},
        "rows": {
            "type": "array",
            "description": "Ровно 3 строки таблицы измерений, в порядке печати: Qнаим, Qперех, Qнаиб",
            "items": {
                "type": "object",
                "properties": {
                    "flow_rate": {"type": ["string", "null"], "description": "Q, м3/ч — уже напечатан на бланке"},
                    "reading_start": {"type": ["string", "null"]},
                    "reading_end": {"type": ["string", "null"]},
                    "volume_standard": {"type": ["string", "null"], "description": "Vэтал, м3"},
                    "confidence": {
                        "type": "number",
                        "description": "Уверенность распознавания этой строки, 0..1",
                    },
                },
                "required": ["confidence"],
            },
        },
    },
    "required": ["legible", "rows"],
}

PROMPT = (
    "Перед тобой фото бланка поверки водосчётчика, выровненное по реперным "
    "меткам. На бланке: тип/модель и заводской номер счётчика (вписаны от "
    "руки), год выпуска, класс счётчика (А/В), тип воды (г/в или х/в, "
    "отмечены галочкой), коэффициент преобразования K, таблица из 3 строк "
    "измерений (Qнаим, Qперех, Qнаиб — расход Q в каждой строке уже "
    "напечатан) с графами «показания в начале», «показания в конце» и "
    "«Vэтал», отметки по пунктам осмотра и, отдельно, блок «НЕПРИГОДЕН» с "
    "галочкой и причиной. Числа в протоколе — с запятой как разделителем "
    "дробной части, верни их строками как написано, не пересчитывай и не "
    "переводи в другой разделитель. Если поле не заполнено или неразборчиво "
    "— верни null, не угадывай и не подставляй правдоподобное значение. Для "
    "каждой строки измерений оцени свою уверенность в распознавании этой "
    "конкретной строки от 0 (не уверен вовсе) до 1 (цифры пропечатаны "
    "разборчиво, сомнений нет) — не завышай её."
)


class RecognitionUnavailable(RuntimeError):
    """Модель не ответила или ответила не по схеме — заводить поверку вручную."""


class YandexVisionClient:
    def __init__(
        self, *, api_key: str | None = None, folder_id: str | None = None,
        model: str | None = None, timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else getattr(settings, "YANDEX_VISION_API_KEY", "")
        self.folder_id = folder_id if folder_id is not None else getattr(settings, "YANDEX_VISION_FOLDER_ID", "")
        self.model = model or getattr(settings, "YANDEX_VISION_MODEL", "") or "qwen3.6-35b-a3b"
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.folder_id)

    def recognize_blank(self, image_bytes: bytes, *, mime_type: str = "image/jpeg") -> dict:
        """Строгий JSON по RESPONSE_SCHEMA. Кидает RecognitionUnavailable, если не вышло."""
        return self.complete_json(
            [
                {"type": "text", "text": PROMPT},
                {"type": "image_bytes", "data": image_bytes, "mime": mime_type},
            ],
            RESPONSE_SCHEMA,
            name="blank_recognition",
        )

    def complete_json(
        self, parts: list[dict], schema: dict, *, name: str, timeout: float | None = None,
    ) -> dict:
        """Один запрос к модели с жёсткой JSON-схемой ответа.

        parts — {"type": "text", "text": ...} и {"type": "image_bytes",
        "data": bytes, "mime": ...}; картинки уходят data-URL. Используется
        и для бланков, и для чтения описаний типа (apps.arshin.limits).
        """
        if not self.is_configured:
            raise RecognitionUnavailable("YANDEX_VISION_API_KEY/YANDEX_VISION_FOLDER_ID не заданы")

        content = []
        for part in parts:
            if part.get("type") == "image_bytes":
                encoded = base64.b64encode(part["data"]).decode("ascii")
                mime = part.get("mime") or "image/jpeg"
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
            else:
                content.append({"type": "text", "text": part.get("text", "")})

        body = {
            "model": f"gpt://{self.folder_id}/{self.model}",
            "temperature": 0.1,
            "messages": [{"role": "user", "content": content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema},
            },
        }

        try:
            response = requests.post(
                getattr(settings, "YANDEX_AI_CHAT_URL", "") or CHAT_URL,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Api-Key {self.api_key}",
                    "OpenAI-Project": self.folder_id,
                },
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise RecognitionUnavailable(f"Распознавание недоступно: {exc}") from exc
        except ValueError as exc:
            raise RecognitionUnavailable("Распознавание вернуло не JSON") from exc

        try:
            message = payload["choices"][0]["message"]["content"]
            data = json.loads(message) if isinstance(message, str) else message
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RecognitionUnavailable(f"Неожиданный формат ответа модели: {exc}") from exc

        if not isinstance(data, dict):
            raise RecognitionUnavailable("Модель вернула не объект")
        return data
