"""Капча Yandex SmartCaptcha на публичной форме заявки.

Без CAPTCHA_SERVER_KEY/CAPTCHA_CLIENT_KEY в .env капча просто выключена — форма
работает как раньше, на одном honeypot (см. apps.hub.api.RequestCreateView),
без отдельной ошибки на каждый запрос. Ключи заводятся в Yandex Cloud
(https://cloud.yandex.ru/docs/smartcaptcha/) — бесплатного лимита с запасом
хватает на такую форму. CLIENT_KEY — публичный, отдаётся анонимно виджету на
форме (см. CaptchaConfigView в apps.hub.api); SERVER_KEY — секретный, только
для проверки токена здесь.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

VALIDATE_URL = "https://smartcaptcha.yandexcloud.net/validate"


def is_configured() -> bool:
    return bool(getattr(settings, "CAPTCHA_SERVER_KEY", "")) and bool(getattr(settings, "CAPTCHA_CLIENT_KEY", ""))


def client_key() -> str:
    return getattr(settings, "CAPTCHA_CLIENT_KEY", "")


def verify(token: str, *, remote_ip: str = "") -> bool:
    """True — Yandex подтвердил токен. Пусто, неверно или Yandex недоступен — False.

    Сетевая недоступность Yandex тоже False, а не пропуск проверки: пока капча
    номинально включена (CAPTCHA_SERVER_KEY задан), лучше попросить заявителя
    попробовать ещё раз, чем молча открыть форму без защиты.
    """
    if not token:
        return False
    try:
        response = requests.post(
            VALIDATE_URL,
            data={"secret": settings.CAPTCHA_SERVER_KEY, "token": token, "ip": remote_ip},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.exception("Не удалось проверить капчу Yandex SmartCaptcha")
        return False
    return data.get("status") == "ok"
