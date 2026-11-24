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
    "apps.assistant",
    "apps.equipment",
    "apps.notify",
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
    "apps.notify.actor.ActorResetMiddleware",
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
        # Постоянные соединения: под нагрузкой не открывать новое на каждый запрос.
        # CONN_HEALTH_CHECKS — перед повторным использованием соединение проверяется,
        # иначе после рестарта postgres первый запрос каждого потока падал бы 500.
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.User"

# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # Тот же JWT, плюс запоминает, кто действует, — чтобы не слать человеку
        # письмо о том, что он сделал сам (apps.notify.actor).
        "apps.notify.actor.ActorJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.AnonRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="20/hour"),
        # Публичная форма заявки — свой бюджет на IP: вход в систему (тоже анонимный)
        # и подсказки не съедают лимит формы, а бот не заваливает очередь заявками.
        "public_request": env("THROTTLE_PUBLIC_REQUEST", default="20/hour"),
        "public_request_burst": env("THROTTLE_PUBLIC_REQUEST_BURST", default="5/min"),
        "address_suggest": "120/min",
        "map_tiles": "3000/min",
        # Смена и сброс пароля (apps.notify.api_password): перебор текущего пароля
        # и рассылка писем «сбросить пароль» по чужим адресам.
        "password_change": env("THROTTLE_PASSWORD_CHANGE", default="10/hour"),
        "password_forgot": env("THROTTLE_PASSWORD_FORGOT", default="5/hour"),
        # Проверка ссылки и сам сброс — своя корзина: ошибка в новом пароле и
        # повтор не съедают лимит «Забыли пароль?».
        "password_reset": env("THROTTLE_PASSWORD_RESET", default="30/hour"),
    },
    # За Caddy REMOTE_ADDR — адрес прокси, и без этого все посетители сайта делили бы
    # один анонимный лимит на всех. 1 — один доверенный прокси перед gunicorn (prod).
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    "PAGE_SIZE": 50,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    # Метка пароля в токене: сменили пароль — старые входы перестают работать
    # (проверка — apps.notify.actor, мягкая к токенам, выданным до этой версии).
    "CHECK_REVOKE_TOKEN": True,
    "TOKEN_REFRESH_SERIALIZER": "apps.notify.actor.StampedTokenRefreshSerializer",
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
# Распознавание бумажных бланков (Yandex AI Studio)
# --------------------------------------------------------------------------
# Без ключей распознавание выключено — apps.hub.yandex_vision.is_configured,
# поверка заводится с тем же бланком вручную (тот же приём, что с DaData).
YANDEX_VISION_API_KEY = env("YANDEX_VISION_API_KEY", default="")
YANDEX_VISION_FOLDER_ID = env("YANDEX_VISION_FOLDER_ID", default="")
YANDEX_VISION_MODEL = env("YANDEX_VISION_MODEL", default="qwen3.6-35b-a3b")
# Свой адрес chat/completions — для прокси или проверки на заглушке; пусто — облако Yandex.
YANDEX_AI_CHAT_URL = env("YANDEX_AI_CHAT_URL", default="")

# --------------------------------------------------------------------------
# ФГИС «Аршин»
# --------------------------------------------------------------------------
# Открытый реестр ФИФ ОЕИ — читаем справочники и подсказываем типы СИ.
ARSHIN_BASE_URL = env("ARSHIN_BASE_URL", default="https://fgis.gost.ru/fundmetrology/eapi")
# Скачивать методику и описание типа в фоне (Celery), когда заводится тип из ФИФ.
FIF_DOCUMENTS_AUTO_DOWNLOAD = env.bool("FIF_DOCUMENTS_AUTO_DOWNLOAD", default=True)
# Как ходить в Госреестр (поиск типов, карточка): auto — через MCP-сервер РСТ (RST_MCP_URL), если задан,
# при его отказе — напрямую в ФГИС; mcp / direct — только этот путь. ФГИС отвечает только российским адресам.
FIF_TRANSPORT = env("FIF_TRANSPORT", default="auto")
# Прошлые поверки по заводскому номеру (apps.arshin.vri): за сколько прошлых лет искать во ФГИС.
# Каждый год — отдельный запрос: за несколько лет сразу ФГИС не успевает ответить.
FIF_VRI_YEARS_BACK = env.int("FIF_VRI_YEARS_BACK", default=7)
# Ключи из старого config.yaml. Что именно они открывают — надо выяснить
# перед реализацией выгрузки сведений о поверке.
ARSHIN_PUBLIC_KEY = env("ARSHIN_PUBLIC_KEY", default="")
ARSHIN_PRIVATE_KEY = env("ARSHIN_PRIVATE_KEY", default="")

# --------------------------------------------------------------------------
# Подсказка адресов (DaData) — этап 2, роутинг заявок по району
# --------------------------------------------------------------------------
# Пусто — форма заявки на сайте падает на обычное текстовое поле адреса,
# роутинг по району в этом случае просто не срабатывает (see apps.hub.address).
DADATA_API_KEY = env("DADATA_API_KEY", default="")

# Координаты адресов командировки для карты (apps.logistics.geocode):
# auto — DaData, если есть ключ, иначе Nominatim (OpenStreetMap); off — не искать.
GEOCODER = env("GEOCODER", default="auto")
GEOCODER_REGION = env("GEOCODER_REGION", default="Кировская область")

# Тайлы карты командировок идут через наш сервер и кэшируются на диске
# (apps.logistics.tiles). Источник — любой растровый с шаблоном {z}/{x}/{y}.
MAP_TILES_UPSTREAM = env("MAP_TILES_UPSTREAM", default="https://tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILES_CONTACT = env("MAP_TILES_CONTACT", default="")
MAP_TILES_REFERER = env("MAP_TILES_REFERER", default="")

# Маршрут дня (apps.logistics.planner): время в пути — 2ГИС Distance Matrix с пробками
# (ключ — platform.2gis.ru, начать можно с демо-ключа), без ключа — оценка по прямой.
DGIS_API_KEY = env("DGIS_API_KEY", default="")
# Откуда выезжаем по умолчанию. Координаты точнее; без них — найдём по адресу.
ROUTE_BASE_ADDRESS = env("ROUTE_BASE_ADDRESS", default="Киров, улица Красноармейская, 43А")
ROUTE_BASE_LAT = env.float("ROUTE_BASE_LAT", default=None)
ROUTE_BASE_LON = env.float("ROUTE_BASE_LON", default=None)
# Выезды по городу: в какой город, сколько минут клиента ждать от обещанного времени.
CITY_NAME = env("CITY_NAME", default="Киров")
CITY_WINDOW_MINUTES = env.int("CITY_WINDOW_MINUTES", default=120)  # клиенту называют интервал в 2 часа
# Рабочий день — граница маршрута; в командировке адреса, что не влезли, уходят на следующий день.
WORK_DAY_START = env("WORK_DAY_START", default="08:00")
WORK_DAY_END = env("WORK_DAY_END", default="17:00")

# --------------------------------------------------------------------------
# Капча (Yandex SmartCaptcha) — публичная форма заявки
# --------------------------------------------------------------------------
# Пусто — форма работает как раньше, на одном honeypot-поле (см. apps.hub.captcha
# и apps.hub.api.RequestCreateView). Ключи — в Yandex Cloud, бесплатного лимита
# хватает на такую форму: https://cloud.yandex.ru/docs/smartcaptcha/
CAPTCHA_CLIENT_KEY = env("CAPTCHA_CLIENT_KEY", default="")  # публичный — отдаётся виджету
CAPTCHA_SERVER_KEY = env("CAPTCHA_SERVER_KEY", default="")  # секретный — только для проверки

# --------------------------------------------------------------------------
# Telegram-уведомления
# --------------------------------------------------------------------------
# Пусто — apps.core.telegram.send_message() только логирует и ничего не шлёт.
# Токен получают у @BotFather. Сотрудник привязывается сам, кодом из своего
# профиля в EI_doc — см. apps.core.telegram.handle_update и manage.py set_telegram_webhook.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
# Секрет в пути вебхука (/api/telegram/webhook/<секрет>/) и в заголовке
# X-Telegram-Bot-Api-Secret-Token — без него любой в интернете мог бы слать
# сюда поддельные апдейты. Генерируется один раз ./deploy.sh, как SECRET_KEY.
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")
# Необязательно: имя бота без @, только чтобы показать его в подсказке
# сотруднику в профиле ("напишите @имя_бота"). Не участвует в проверках.
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="")

# --------------------------------------------------------------------------
# Ассистент-метролог (чат с LLM, см. apps/assistant и claude/assistant.md)
# --------------------------------------------------------------------------
# OpenRouter — каталог сильных моделей (Claude, GPT, Gemini…). Пусто — в выборе
# остаются только модели Yandex AI Studio (ключи YANDEX_VISION_* выше).
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
# Прокси только для запросов к OpenRouter (с российских адресов он отвечает 403).
# http://user:pass@host:port или socks5h://user:pass@host:port. Yandex, ФГИС и РСТ
# ходят напрямую.
OPENROUTER_PROXY = env("OPENROUTER_PROXY", default="")
# Модель по умолчанию «провайдер:модель»; правится и на экране «Контекст → Настройки».
ASSISTANT_DEFAULT_MODEL = env("ASSISTANT_DEFAULT_MODEL", default="")
# Модели Yandex AI Studio, которые показывать в выборе (через запятую).
ASSISTANT_YANDEX_MODELS = env.list(
    "ASSISTANT_YANDEX_MODELS", default=["qwen3-235b-a22b-fp8/latest", "yandexgpt/latest"]
)
# Адрес сайта — OpenRouter показывает его в своей статистике (HTTP-Referer).
ASSISTANT_PUBLIC_URL = env("ASSISTANT_PUBLIC_URL", default="")
# Веб-поиск: Yandex Search API v2. Пусто — берётся YANDEX_VISION_API_KEY (тот же
# сервисный аккаунт, ему нужна роль search-api.webSearch.user).
YANDEX_SEARCH_API_KEY = env("YANDEX_SEARCH_API_KEY", default="")
# RSTMetrology_MCP — заводится сам при migrate, если адрес задан.
RST_MCP_URL = env("RST_MCP_URL", default="")
RST_MCP_TOKEN = env("RST_MCP_TOKEN", default="")
# celery — прогон агента в воркере (очередь assistant); thread — в потоке веб-процесса
# (для локального запуска без Celery); sync — прямо в запросе (тесты).
ASSISTANT_RUN_MODE = env("ASSISTANT_RUN_MODE", default="celery")

# Ассистент на agentkit (сервис agent/ рядом с EI_doc). Пусто AGENT_URL — работает встроенный чат.
# AGENT_URL — адрес веб-чата агента для фрейма вкладки «Ассистент» (https://agent.example.ru);
# AGENT_LOGIN_SECRET — подпись токенов входа (= AGENT_HOST_LOGIN_SECRET у агента);
# AGENT_HOST_TOKEN — токен, с которым агент ходит в MCP-сервер EI_doc /api/assistant/agent/mcp
# (= token сервера «ei» в agent/mcp.json); AGENT_INTERNAL_URL + AGENT_SERVICE_TOKEN — для команды
# agent_migrate_memory (перенос личной памяти в агента).
AGENT_URL = env("AGENT_URL", default="")
AGENT_LOGIN_SECRET = env("AGENT_LOGIN_SECRET", default="")
AGENT_HOST_TOKEN = env("AGENT_HOST_TOKEN", default="")
AGENT_INTERNAL_URL = env("AGENT_INTERNAL_URL", default="http://ei-agent:8080")
AGENT_SERVICE_TOKEN = env("AGENT_SERVICE_TOKEN", default="")

# --------------------------------------------------------------------------
# Почта и уведомления (apps.notify, как в openseller — решение 069)
# --------------------------------------------------------------------------
# Пустой SMTP_HOST — письма только пишутся в журнал (apps.notify.backends.LogBackend),
# «Забыли пароль?» честно отвечает, что почта не настроена.
# Порт 465 — сразу TLS (Яндекс 360, Mail.ru), 587 — STARTTLS. SMTP_TLS: implicit | starttls;
# пусто — по порту. Открытым текстом пароль на SMTP не отправляется никогда.
from apps.notify.tlsmode import implicit_tls as _implicit_tls  # noqa: E402 — без Django, только логика порта

SMTP_HOST = env("SMTP_HOST", default="")
SMTP_PORT = env.int("SMTP_PORT", default=465)
SMTP_TLS = env("SMTP_TLS", default="")
SMTP_USERNAME = env("SMTP_USERNAME", default="")
SMTP_PASSWORD = env("SMTP_PASSWORD", default="")
MAIL_FROM = env("MAIL_FROM", default="") or SMTP_USERNAME or "EI_doc <noreply@localhost>"

EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend" if SMTP_HOST else "apps.notify.backends.LogBackend"
)
EMAIL_HOST = SMTP_HOST
EMAIL_PORT = SMTP_PORT
EMAIL_HOST_USER = SMTP_USERNAME
EMAIL_HOST_PASSWORD = SMTP_PASSWORD
EMAIL_USE_SSL = bool(SMTP_HOST) and _implicit_tls(SMTP_PORT, SMTP_TLS)
EMAIL_USE_TLS = bool(SMTP_HOST) and not EMAIL_USE_SSL
EMAIL_TIMEOUT = env.int("SMTP_TIMEOUT", default=30)
DEFAULT_FROM_EMAIL = MAIL_FROM
SERVER_EMAIL = MAIL_FROM

# Адрес кабинета для ссылок в письмах («https://ei-doc.eicounters.ru»). Пусто — первый
# из CSRF_TRUSTED_ORIGINS.
APP_PUBLIC_URL = (env("APP_PUBLIC_URL", default="") or (CSRF_TRUSTED_ORIGINS[:1] or [""])[0]).rstrip("/")
# Ссылка «задать новый пароль» живёт час и срабатывает один раз (как в openseller).
PASSWORD_RESET_TIMEOUT = 60 * 60
# Писем сброса на один адрес в час (сверх — молча, ответ тот же).
PASSWORD_FORGOT_PER_ADDRESS = env.int("PASSWORD_FORGOT_PER_ADDRESS", default=3)
# Через сколько секунд после события собирать письмо: назначения пачкой — одним письмом.
NOTIFY_FLUSH_DELAY = env.int("NOTIFY_FLUSH_DELAY", default=20)
# False — собирать и отправлять сразу в процессе (тесты, локальный запуск без Celery).
NOTIFY_ASYNC = env.bool("NOTIFY_ASYNC", default=True)

# --------------------------------------------------------------------------
# Celery
# --------------------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TIMEZONE = TIME_ZONE
# Ассистент — своя очередь и свой воркер: долгий поиск по ФГИС не должен
# задерживать распознавание бланков и повторяющиеся задачи.
CELERY_TASK_ROUTES = {"apps.assistant.tasks.*": {"queue": "assistant"}}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL", default=env("REDIS_URL", default="redis://localhost:6379/1")),
        "TIMEOUT": 300,
    }
}
# Ответы внешних сервисов (DaData: реквизиты по ИНН, подсказки, геокодер) кэшируются в
# Redis — apps.core.cache.remember. В тестах выключено, чтобы моки не «залипали».
EXTERNAL_CACHE_ENABLED = env.bool("EXTERNAL_CACHE_ENABLED", default=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{levelname} {asctime} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
