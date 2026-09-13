"""Настройки для быстрого локального прогона тестов на SQLite.

В CI и перед релизом тесты гоняются на postgres (config.settings.dev),
потому что select_for_update и JSONB-индексы SQLite не проверяет.
"""

from .base import *  # noqa: F403

DEBUG = False
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
