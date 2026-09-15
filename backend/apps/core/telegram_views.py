"""Вебхук Telegram-бота.

Отдельный файл от apps.core.telegram (там — только отправка и разбор
апдейта): вьюха — это транспорт (проверка секрета, разбор запроса), логика
привязки живёт в apps.core.telegram.handle_update, чтобы её можно было
дёрнуть и из теста, и из shell без HTTP.
"""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.telegram import handle_update

logger = logging.getLogger(__name__)


class TelegramWebhookView(APIView):
    """POST /api/telegram/webhook/<secret>/

    Секрет — и в пути (не только тот, кто знает URL, может дёргать), и,
    если Bot API его поддерживает на этом деплое, в заголовке
    X-Telegram-Bot-Api-Secret-Token (передаётся в setWebhook, см.
    apps.core.telegram.set_webhook). Несовпадение молча отвечает 200 с
    ok=False — Telegram не должен получить повод ретраить чужой секрет.
    """

    permission_classes = [AllowAny]

    def post(self, request, secret: str):
        expected = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not expected or secret != expected or (header_secret and header_secret != expected):
            logger.warning("Telegram-вебхук: неверный секрет")
            return Response({"ok": False})

        handle_update(request.data or {})
        return Response({"ok": True})
