"""Непойманные исключения API — понятным JSON вместо голой «Ошибка 500».

DRF сам превращает в ответ только свои исключения (ValidationError, PermissionDenied…);
всё остальное Django отдаёт HTML-страницей 500, и интерфейс показывает лишь код. Здесь
такие ошибки пишутся в журнал с коротким кодом, а человек видит, что случилось и этот код —
по нему ошибку легко найти в логах backend.
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger("apps.api")


def _is_connection_error(exc: Exception) -> str:
    """Имя недоступного сервиса, если исключение — потеря связи с Redis/брокером."""
    names = {f"{type(e).__module__}.{type(e).__name__}" for e in _chain(exc)}
    if any(n.startswith(("redis.", "kombu.")) for n in names):
        return "Redis"
    return ""


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response
    code = uuid.uuid4().hex[:8]
    view = context.get("view")
    log.exception("Необработанная ошибка API [%s] в %s", code, type(view).__name__ if view else "?")
    service = _is_connection_error(exc)
    if service:
        return Response({"detail": f"Сервер не может связаться с {service} — он не запущен или недоступен. "
                                   f"Сообщите администратору (код ошибки {code}).", "code": code}, status=503)
    reason = f"{type(exc).__name__}: {exc}" if settings.DEBUG else type(exc).__name__
    return Response({"detail": f"Внутренняя ошибка сервера ({reason}). Подробности — в журнале backend, "
                               f"код ошибки {code}.", "code": code}, status=500)
