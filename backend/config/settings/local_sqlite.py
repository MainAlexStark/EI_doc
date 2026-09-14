"""Запуск без Docker и без PostgreSQL — посмотреть админку и справочники.

    DJANGO_SETTINGS_MODULE=config.settings.local_sqlite python manage.py migrate
    DJANGO_SETTINGS_MODULE=config.settings.local_sqlite python manage.py seed_catalog
    DJANGO_SETTINGS_MODULE=config.settings.local_sqlite python manage.py runserver

Для боевой работы не годится: нумерация держится на SELECT … FOR UPDATE,
которого в SQLite нет.
"""

from .base import *  # noqa: F403
from .base import BACKEND_DIR

DEBUG = True
ALLOWED_HOSTS = ["*"]
DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BACKEND_DIR / "local.sqlite3"}
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
