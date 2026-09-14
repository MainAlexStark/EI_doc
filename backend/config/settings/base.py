"""Базовые настройки EI_doc.

Переменные окружения читаются из .env в корне репозитория (django-environ).
Ничего секретного в коде — см. .env.example.
"""

from datetime import timedelta
from pathlib import Path

import environ

# backend/config/settings/base.py -> backend/config -> backend -> корень репозитория
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_DIR = BACKEND_DIR.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
)
environ.Env.read_env(REPO_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# --------------------------------------------------------------------------
# Приложения
# --------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "simple_history",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.catalog",
    "apps.verification",
    "apps.hub",
    "apps.tasks",
    "apps.logistics",
    "apps.sync",
    "apps.arshin",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Статику раньше отдавал nginx (alias /static/); общий Caddy делает
    # только TLS и маршрутизацию по доменам, файлы сам не отдаёт — поэтому
    # whitenoise отдаёт их прямо из процесса Django, со сжатием и
    # far-future заголовками из коробки.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BACKEND_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# База данных
# --------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 60,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.User"

# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.AnonRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {"anon": "20/hour"},
    "PAGE_SIZE": 50,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# Полевые устройства: поверитель в недельной командировке не должен разлогиниться
# посреди выезда. Токен привязывается к устройству и отзывается из админки.
FIELD_DEVICE_REFRESH_LIFETIME = timedelta(days=30)

SPECTACULAR_SETTINGS = {
    "TITLE": "EI_doc API",
    "DESCRIPTION": "Документооборот поверочных работ ООО «Единица Измерения»",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --------------------------------------------------------------------------
# Локаль
# --------------------------------------------------------------------------
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Kirov"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BACKEND_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Собранный фронтенд (frontend/dist) кладёт сюда deploy/backend.Dockerfile
# отдельной стадией сборки. Локально без Docker папки нет — тогда фронт
# поднимают отдельным процессом: cd frontend && npm run dev (см. README).
FRONTEND_DIST_DIR = BACKEND_DIR / "frontend_dist"
STATICFILES_DIRS = [("frontend", FRONTEND_DIST_DIR)] if FRONTEND_DIST_DIR.is_dir() else []
MEDIA_URL = "media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BACKEND_DIR / "media"))

# Протоколы и сканы бланков: право на файл проверяет вьюха, сам файл
# стримит Django (раньше это был X-Accel-Redirect через nginx — общий
# Caddy так не умеет). На объёмах этой конторы — не узкое место; если
# станет одним, есть SENDFILE_BACKEND=sendfile.backends.xsendfile для
# отдельного файлового сервера за Caddy, меняется одна строка.
SENDFILE_BACKEND = env("SENDFILE_BACKEND", default="django_sendfile.backends.simple")
SENDFILE_ROOT = MEDIA_ROOT
SENDFILE_URL = MEDIA_URL

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# Рендер протоколов
# --------------------------------------------------------------------------
# Бинарник Typst. В образе ставится Dockerfile'ом и лежит в PATH; переменная
# нужна, только если он в нестандартном месте.
TYPST_BINARY = env("TYPST_BINARY", default="")

# --------------------------------------------------------------------------
# ФГИС «Аршин»
# --------------------------------------------------------------------------
# Открытый реестр ФИФ ОЕИ — читаем справочники и подсказываем типы СИ.
ARSHIN_BASE_URL = env("ARSHIN_BASE_URL", default="https://fgis.gost.ru/fundmetrology/eapi")
# Ключи из старого config.yaml. Что именно они открывают — надо выяснить
# перед реализацией выгрузки сведений о поверке.
ARSHIN_PUBLIC_KEY = env("ARSHIN_PUBLIC_KEY", default="")
ARSHIN_PRIVATE_KEY = env("ARSHIN_PRIVATE_KEY", default="")

# --------------------------------------------------------------------------
# Celery
# --------------------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TIMEZONE = TIME_ZONE

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/1"),
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{levelname} {asctime} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
