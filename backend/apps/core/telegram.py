"""Telegram-уведомления сотрудникам.

Токен бота (TELEGRAM_BOT_TOKEN) не заведён на момент этапа 2 — интеграция
заложена, но без него send_message() только логирует и ничего не шлёт.
Как только токен появится, реальная отправка включится сама, без правок кода.

Привязка сотрудника к чату — руками (Employee.telegram_chat_id): у бота нет
собственного шага онбординга (/start -> сохранить chat_id) — это отдельная
небольшая задача на потом, не блокирующая остальной этап 2.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramSendError(RuntimeError):
    """Не смогли отправить — вызывающий код это не считает провалом операции."""


def is_configured() -> bool:
    return bool(getattr(settings, "TELEGRAM_BOT_TOKEN", ""))


def send_message(chat_id: str, text: str) -> bool:
    """Отправить сообщение. Возвращает False, если отправка не выполнена —

    это никогда не бросает наружу: уведомление — побочный эффект действия
    (назначили наряд, поставили задачу), а не само действие, и не должно
    ронять его из-за недоступного Telegram или отсутствующего токена.
    """
    if not chat_id:
        return False
    if not is_configured():
        logger.info("Telegram не настроен (TELEGRAM_BOT_TOKEN пуст) — сообщение для %s: %s", chat_id, text)
        return False

    url = TELEGRAM_API_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        response = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=5.0)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Не удалось отправить сообщение в Telegram (chat_id=%s)", chat_id)
        return False
    return True
