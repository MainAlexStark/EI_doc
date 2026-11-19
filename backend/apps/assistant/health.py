"""Работает ли встроенный ассистент — до того, как человек напишет сообщение.

Без этой проверки поломка видна только как «Ошибка 500» после отправки (Redis лежит —
падает постановка задачи) или как вечное «думает…» (Redis жив, но воркер очереди
``assistant`` не запущен: задача ждёт в очереди, отвечать некому).

``problems()`` — список понятных причин, пустой — всё в порядке. Ответ кэшируется в памяти
процесса на несколько секунд: экран чата спрашивает статус часто, а пинг воркера — до секунды.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings

from apps.assistant import llm

log = logging.getLogger(__name__)

TTL_OK = 60.0  # опрос воркеров ждёт ответа до секунды — не на каждое сообщение
TTL_FAIL = 5.0
_cache: tuple[float, list[str]] | None = None


class Unavailable(Exception):
    """Ассистент сейчас не может принять сообщение; текст — для человека."""


def _broker_problem() -> str:
    url = settings.CELERY_BROKER_URL
    if not url.startswith("redis"):
        return ""
    import redis

    try:
        redis.Redis.from_url(url, socket_connect_timeout=1.5, socket_timeout=1.5).ping()
    except Exception as exc:  # noqa: BLE001 — любая ошибка связи значит одно и то же
        log.warning("assistant health: redis %s недоступен: %s", url, exc)
        return (f"Нет связи с Redis ({_host(url)}) — очередь задач ассистента не работает. "
                "Запустите Redis (docker compose up -d redis) или для локального запуска без Celery "
                "задайте ASSISTANT_RUN_MODE=thread.")
    return ""


def _worker_problem() -> str:
    from config.celery import app

    try:
        replies = app.control.inspect(timeout=1.0).active_queues() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("assistant health: опрос воркеров не удался: %s", exc)
        return f"Не удалось опросить воркеры Celery: {exc}"
    if any(q.get("name") == "assistant" for queues in replies.values() for q in queues or []):
        return ""
    return ("Не запущен воркер очереди «assistant» — сообщения будут ждать без ответа. "
            "Запустите его: docker compose up -d assistant-worker "
            "(или celery -A config worker -Q assistant).")


def _host(url: str) -> str:
    return url.split("@")[-1].split("://")[-1].split("/")[0]


def problems(fresh: bool = False) -> list[str]:
    global _cache
    now = time.monotonic()
    if not fresh and _cache and now < _cache[0]:
        return list(_cache[1])
    found: list[str] = []
    if not llm.default_model():
        found.append("Не настроена ни одна модель: задайте OPENROUTER_API_KEY или ключи Yandex в .env "
                     "и пересоздайте контейнеры.")
    queue = queue_problems()
    if queue:
        found.append(queue)
    _cache = (now + (TTL_FAIL if found else TTL_OK), found)
    return list(found)


def queue_problems() -> str:
    """Есть ли кому выполнить прогон (только для режима celery; thread/sync выполняют сами)."""
    if getattr(settings, "ASSISTANT_RUN_MODE", "celery") != "celery":
        return ""
    return _broker_problem() or _worker_problem()


def forget() -> None:
    """Сбросить кэш — например, когда постановка в очередь только что упала."""
    global _cache
    _cache = None


def ensure_available() -> None:
    """Перед постановкой прогона: есть ли очередь и воркер (результат кэшируется, см. TTL_OK)."""
    # Модель проверяет сам запрос (у чата она своя); здесь — только очередь и воркер.
    found = [p for p in problems() if not p.startswith("Не настроена ни одна модель")]
    if found:
        raise Unavailable(" ".join(found))
