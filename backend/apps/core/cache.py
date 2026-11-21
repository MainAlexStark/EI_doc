"""Кэш ответов внешних сервисов и справочников.

Внешние сервисы (DaData, геокодеры) отвечают сотни миллисекунд и считают
запросы по тарифу, а спрашивают их одним и тем же: тот же ИНН на каждой
приёмке, тот же адрес на каждой записи. ``remember`` держит удачный ответ в
Redis; неудачу не кэширует — сервис мог просто моргнуть.

Недоступный Redis не роняет запрос: тогда просто идём в сервис напрямую.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_MISS = object()


def key(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return f"ei:{prefix}:{hashlib.md5(raw.encode()).hexdigest()}"


def enabled() -> bool:
    return bool(getattr(settings, "EXTERNAL_CACHE_ENABLED", True))


def remember(cache_key: str, ttl: int, compute: Callable[[], Any], *, keep: Callable[[Any], bool] = bool) -> Any:
    """Значение из кэша или ``compute()``; в кэш кладётся только то, что ``keep`` счёл удачным."""
    if not enabled():
        return compute()
    try:
        cached = cache.get(cache_key, _MISS)
    except Exception as exc:  # noqa: BLE001 — Redis лежит: работаем без кэша
        logger.warning("cache: get %s: %s", cache_key, exc)
        return compute()
    if cached is not _MISS:
        return cached
    value = compute()
    if keep(value):
        try:
            cache.set(cache_key, value, ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache: set %s: %s", cache_key, exc)
    return value


def forget(*cache_keys: str) -> None:
    try:
        cache.delete_many(list(cache_keys))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache: delete %s: %s", cache_keys, exc)
