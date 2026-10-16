"""Клиент LLM: OpenRouter и Yandex AI Studio через один OpenAI-совместимый протокол.

Идентификатор модели в EI_doc — «провайдер:модель»:

* ``openrouter:anthropic/claude-sonnet-4.5`` — любая модель каталога OpenRouter;
* ``yandex:qwen3-235b-a22b-fp8/latest`` — модель Yandex AI Studio (данные не
  покидают РФ), ключ и каталог те же, что у распознавания бланков.

Ответ стримится (SSE): текст отдаётся по кусочкам в ``on_text``, вызовы
инструментов собираются из дельт целиком.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Iterator

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1"
YANDEX_URL = "https://ai.api.cloud.yandex.net/v1"


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""

    def parsed(self) -> dict:
        if not self.arguments.strip():
            return {}
        try:
            value = json.loads(self.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"аргументы инструмента — не JSON: {exc}") from exc
        return value if isinstance(value, dict) else {"value": value}


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Провайдеры
# ---------------------------------------------------------------------------
def split_model(model_id: str) -> tuple[str, str]:
    if ":" in model_id:
        provider, name = model_id.split(":", 1)
        if provider in ("openrouter", "yandex"):
            return provider, name
    return "openrouter", model_id


def provider_configured(provider: str) -> bool:
    if provider == "openrouter":
        return bool(settings.OPENROUTER_API_KEY)
    if provider == "yandex":
        return bool(settings.YANDEX_VISION_API_KEY and settings.YANDEX_VISION_FOLDER_ID)
    return False


def _endpoint(provider: str, name: str) -> tuple[str, dict, str]:
    if provider == "yandex":
        folder = settings.YANDEX_VISION_FOLDER_ID
        headers = {
            "Authorization": f"Api-Key {settings.YANDEX_VISION_API_KEY}",
            "OpenAI-Project": folder,
        }
        model = name if name.startswith("gpt://") else f"gpt://{folder}/{name}"
        return YANDEX_URL, headers, model
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "HTTP-Referer": settings.ASSISTANT_PUBLIC_URL or "https://ei-doc.local",
        "X-Title": "EI_doc",
    }
    return OPENROUTER_URL, headers, name


def _iter_sse(response) -> Iterator[dict]:
    for raw in response.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue  # комментарии «: OPENROUTER PROCESSING» и пр.
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def chat(
    model_id: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    on_text: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 8000,
    timeout: float = 300,
) -> Completion:
    provider, name = split_model(model_id)
    if not provider_configured(provider):
        raise LLMError(
            "Не задан ключ OPENROUTER_API_KEY" if provider == "openrouter"
            else "Не заданы YANDEX_VISION_API_KEY / YANDEX_VISION_FOLDER_ID"
        )
    base, headers, model = _endpoint(provider, name)
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if provider == "openrouter":
        body["usage"] = {"include": True}

    try:
        response = requests.post(
            f"{base}/chat/completions", headers=headers, json=body, stream=True, timeout=(15, timeout),
        )
    except requests.RequestException as exc:
        raise LLMError(f"Модель недоступна: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json()
            detail = detail.get("error", {}).get("message") or json.dumps(detail, ensure_ascii=False)
        except ValueError:
            detail = response.text[:500]
        raise LLMError(f"{provider} ответил {response.status_code}: {detail}")

    result = Completion()
    calls: dict[int, ToolCall] = {}
    try:
        for event in _iter_sse(response):
            if should_stop and should_stop():
                result.finish_reason = "cancelled"
                break
            if event.get("error"):
                message = event["error"].get("message") if isinstance(event["error"], dict) else event["error"]
                raise LLMError(f"Ошибка модели: {message}")
            if event.get("usage"):
                result.usage = event["usage"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning"):
                    result.reasoning += delta["reasoning"]
                text = delta.get("content")
                if text:
                    result.text += text
                    if on_text:
                        on_text(text)
                for item in delta.get("tool_calls") or []:
                    index = item.get("index", len(calls))
                    call = calls.setdefault(index, ToolCall(id="", name=""))
                    if item.get("id"):
                        call.id = item["id"]
                    function = item.get("function") or {}
                    if function.get("name"):
                        call.name += function["name"]
                    if function.get("arguments"):
                        call.arguments += function["arguments"]
                if choice.get("finish_reason"):
                    result.finish_reason = choice["finish_reason"]
    except requests.RequestException as exc:
        raise LLMError(f"Связь с моделью оборвалась: {exc}") from exc
    finally:
        response.close()

    for index in sorted(calls):
        call = calls[index]
        if not call.id:
            call.id = f"call_{index}"
        if call.name:
            result.tool_calls.append(call)
    return result


# ---------------------------------------------------------------------------
# Каталог моделей для выбора в чате
# ---------------------------------------------------------------------------
def _openrouter_models() -> list[dict]:
    cached = cache.get("assistant:openrouter-models")
    if cached is not None:
        return cached
    try:
        response = requests.get(f"{OPENROUTER_URL}/models", timeout=15)
        response.raise_for_status()
        rows = response.json().get("data", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Каталог OpenRouter недоступен: %s", exc)
        return []
    models = []
    for row in rows:
        params = row.get("supported_parameters") or []
        if "tools" not in params:
            continue  # агенту без инструментов делать нечего
        arch = row.get("architecture") or {}
        pricing = row.get("pricing") or {}
        models.append({
            "id": f"openrouter:{row['id']}",
            "name": row.get("name") or row["id"],
            "provider": "openrouter",
            "context_length": row.get("context_length"),
            "vision": "image" in (arch.get("input_modalities") or []),
            "price_in": _per_million(pricing.get("prompt")),
            "price_out": _per_million(pricing.get("completion")),
            "created": row.get("created") or 0,
        })
    models.sort(key=lambda m: -m["created"])
    cache.set("assistant:openrouter-models", models, 6 * 3600)
    return models


def _per_million(value) -> float | None:
    try:
        return round(float(value) * 1_000_000, 3)
    except (TypeError, ValueError):
        return None


def available_models() -> list[dict]:
    models: list[dict] = []
    if provider_configured("yandex"):
        for name in settings.ASSISTANT_YANDEX_MODELS:
            models.append({
                "id": f"yandex:{name}", "name": f"Yandex · {name}", "provider": "yandex",
                "context_length": None, "vision": False, "price_in": None, "price_out": None,
            })
    if provider_configured("openrouter"):
        models.extend(_openrouter_models())
    return models


def model_info(model_id: str) -> dict:
    for model in available_models():
        if model["id"] == model_id:
            return model
    return {"id": model_id, "vision": False, "context_length": None}


def default_model() -> str:
    from apps.assistant.models import AssistantSettings

    configured = AssistantSettings.current().default_model or settings.ASSISTANT_DEFAULT_MODEL
    if configured:
        return configured
    if provider_configured("openrouter"):
        return "openrouter:anthropic/claude-sonnet-4.5"
    if provider_configured("yandex") and settings.ASSISTANT_YANDEX_MODELS:
        return f"yandex:{settings.ASSISTANT_YANDEX_MODELS[0]}"
    return ""
