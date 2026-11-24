"""Настройки для быстрого локального прогона тестов на SQLite.

В CI и перед релизом тесты гоняются на postgres (config.settings.dev),
потому что select_for_update и JSONB-индексы SQLite не проверяет.
"""

from .base import *  # noqa: F403

DEBUG = False
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

FIF_DOCUMENTS_AUTO_DOWNLOAD = False

ASSISTANT_RUN_MODE = "sync"
OPENROUTER_API_KEY = "test-key"
RST_MCP_URL = ""
GEOCODER = "off"
DGIS_API_KEY = ""
ROUTE_BASE_ADDRESS = ""

# Внешние ключи из локального .env не должны менять поведение тестов: с
# заданными CAPTCHA_* публичная форма требует токен и отвечает 400, с DaData —
# идёт в сеть. Тесты, которым ключ нужен, включают его через override_settings.
CAPTCHA_CLIENT_KEY = ""
CAPTCHA_SERVER_KEY = ""
DADATA_API_KEY = ""
YANDEX_VISION_API_KEY = ""
YANDEX_SEARCH_API_KEY = ""
TELEGRAM_BOT_TOKEN = ""
AGENT_URL = ""
EXTERNAL_CACHE_ENABLED = False

# Почта: письма — в память (django.core.mail.outbox), уведомления — сразу, без Celery.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SMTP_HOST = ""
NOTIFY_ASYNC = False
APP_PUBLIC_URL = "https://ei-doc.test"
