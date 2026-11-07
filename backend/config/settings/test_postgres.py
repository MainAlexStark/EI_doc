"""Тесты на PostgreSQL — перед релизом и в CI.

То же, что config.settings.test, но база настоящая: нумерация держится на
select_for_update и частичных уникальных индексах, которых SQLite не проверяет.
Подключение — из DB_* в окружении (как в base.py).
"""

from .base import DATABASES as _PG_DATABASES
from .test import *  # noqa: F403

DATABASES = _PG_DATABASES
DATABASES["default"]["CONN_MAX_AGE"] = 0
