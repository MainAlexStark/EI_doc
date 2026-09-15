"""Регистрирует вебхук Telegram-бота у Bot API.

    python manage.py set_telegram_webhook --url https://ei-doc.example.ru

Без --url берёт первый домен из ALLOWED_HOSTS. Ничего не делает и не падает,
если TELEGRAM_BOT_TOKEN или TELEGRAM_WEBHOOK_SECRET не заданы — команда
дописана в команду запуска контейнера web (docker-compose.yml) и должна
молча пропускать себя, пока токен не завели, как и остальная интеграция.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.telegram import is_webhook_configured, set_webhook


class Command(BaseCommand):
    help = "Зарегистрировать вебхук Telegram-бота (нужны TELEGRAM_BOT_TOKEN и TELEGRAM_WEBHOOK_SECRET)"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--url", help="https://домен, без хвоста — путь допишется сам")
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options) -> None:
        quiet = options["quiet"]
        if not is_webhook_configured():
            if not quiet:
                self.stdout.write("TELEGRAM_BOT_TOKEN или TELEGRAM_WEBHOOK_SECRET не заданы — пропускаю")
            return

        base_url = options.get("url")
        if not base_url:
            hosts = [h for h in settings.ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1", "*")]
            if not hosts:
                self.stderr.write("Не удалось определить домен — укажите --url явно")
                return
            base_url = f"https://{hosts[0]}"

        webhook_url = f"{base_url.rstrip('/')}/api/telegram/webhook/{settings.TELEGRAM_WEBHOOK_SECRET}/"
        if set_webhook(webhook_url):
            self.stdout.write(self.style.SUCCESS(f"Вебхук зарегистрирован: {webhook_url}"))
        else:
            self.stderr.write(self.style.ERROR("Telegram отказал в регистрации вебхука — см. лог"))
