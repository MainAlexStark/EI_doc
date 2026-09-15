"""Telegram-уведомления сотрудникам и онбординг бота.

Токен бота (TELEGRAM_BOT_TOKEN) — из @BotFather, кладётся в .env. Без него
send_message() только логирует и ничего не шлёт: интеграция заложена и
включается сама, без правок кода, как только токен появится.

Привязка сотрудника к чату — через одноразовый код (Employee.telegram_link_code,
см. apps.core.models.Employee.generate_telegram_link_code): сотрудник получает
код в своём профиле в EI_doc, пишет боту /start <код>, вебхук (handle_update
ниже) сверяет код с Employee и сохраняет chat_id сам — руками в админке
заполнять больше не нужно.
"""

from __future__ import annotations

import logging
import re

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"

# Код — 8 hex-символов (apps.core.models.Employee.generate_telegram_link_code).
# Принимаем и "/start ABCD1234", и "start=ABCD1234" (deep link), и голый код.
_CODE_RE = re.compile(r"([0-9A-Fa-f]{8})")


class TelegramSendError(RuntimeError):
    """Не смогли отправить — вызывающий код это не считает провалом операции."""


def is_configured() -> bool:
    return bool(getattr(settings, "TELEGRAM_BOT_TOKEN", ""))


def is_webhook_configured() -> bool:
    return is_configured() and bool(getattr(settings, "TELEGRAM_WEBHOOK_SECRET", ""))


def send_message(chat_id: str, text: str) -> bool:
    """Отправить сообщение. Возвращает False, если отправка не выполнена —

    это никогда не бросает наружу: уведомление — побочный эффект действия
    (назначили наряд, поставили задачу, привязали Telegram), а не само
    действие, и не должно ронять его из-за недоступного Telegram или
    отсутствующего токена.
    """
    if not chat_id:
        return False
    if not is_configured():
        logger.info("Telegram не настроен (TELEGRAM_BOT_TOKEN пуст) — сообщение для %s: %s", chat_id, text)
        return False

    url = TELEGRAM_API_URL.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendMessage")
    try:
        response = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=5.0)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Не удалось отправить сообщение в Telegram (chat_id=%s)", chat_id)
        return False
    return True


def set_webhook(url: str) -> bool:
    """Зарегистировать вебхук в Bot API. Вызывается management-командой при деплое."""
    if not is_configured():
        return False
    api_url = TELEGRAM_API_URL.format(token=settings.TELEGRAM_BOT_TOKEN, method="setWebhook")
    payload = {"url": url, "allowed_updates": ["message"]}
    secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
    if secret:
        payload["secret_token"] = secret
    try:
        response = requests.post(api_url, json=payload, timeout=10.0)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        logger.exception("Не удалось зарегистрировать Telegram-вебхук")
        return False
    if not data.get("ok"):
        logger.error("Telegram отказал в регистрации вебхука: %s", data)
        return False
    return True


def handle_update(payload: dict) -> None:
    """Обработать один Update от Telegram (вызывается из вебхука).

    Единственное, что бот умеет сейчас, — привязка chat_id по одноразовому
    коду. Любой другой текст получает подсказку. Ошибки любого рода не
    бросаем наружу: вебхук должен ответить Telegram 200 в любом случае,
    иначе Bot API считает доставку неуспешной и повторяет её.
    """
    from apps.core.models import Employee  # локальный импорт — не тянуть модели в модуль отправки

    try:
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            return
        chat_id = str(message.get("chat", {}).get("id") or "")
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        match = _CODE_RE.search(text)
        if not match:
            if text.startswith("/start"):
                send_message(
                    chat_id,
                    "Здравствуйте! Чтобы получать уведомления, откройте свой профиль в EI_doc, "
                    "получите код привязки и пришлите его сюда командой /start <код>.",
                )
            return

        code = match.group(1).upper()
        employee = (
            Employee.objects.filter(telegram_link_code__iexact=code)
            .exclude(telegram_link_code="")
            .first()
        )
        if not employee or not employee.telegram_link_code_expires_at:
            send_message(chat_id, "Код не найден. Получите новый в своём профиле в EI_doc.")
            return
        if employee.telegram_link_code_expires_at < timezone.now():
            send_message(chat_id, "Код истёк (действует 15 минут). Получите новый в своём профиле в EI_doc.")
            return

        employee.telegram_chat_id = chat_id
        employee.telegram_link_code = ""
        employee.telegram_link_code_expires_at = None
        employee.save(update_fields=["telegram_chat_id", "telegram_link_code", "telegram_link_code_expires_at"])
        send_message(chat_id, f"Готово, {employee.full_name}! Уведомления EI_doc теперь приходят сюда.")
    except Exception:  # noqa: BLE001 — вебхук обязан ответить 200 в любом случае
        logger.exception("Ошибка обработки Telegram-вебхука")
