"""Диагностические вьюхи core.

Отдельный файл, а не что-то в api.py: это не часть домена, читает его
deploy.sh (через curl), не браузер и не клиент API.
"""

import re

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import Http404, HttpResponse, JsonResponse


def healthz(request):
    """Проверка для deploy.sh: 200 значит «база отвечает, приложение живо».

    Без пути в БД health-check врёт — gunicorn может слушать порт, пока
    миграции ещё падают или postgres недоступен, и тогда деплой решит,
    что всё поднялось, хотя запросы будут получать 500.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "db unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def frontend_index(request):
    """Отдаёт index.html собранного фронтенда журнала.

    Сам SPA не использует клиентский роутинг (переключение вкладок — внутри
    одной страницы, см. frontend/src/App.tsx), поэтому единственный путь,
    который это отдаёт — корень "/"; JS и CSS идут отдельно через whitenoise
    (STATICFILES_DIRS = frontend, см. settings/base.py).
    """
    index_path = settings.FRONTEND_DIST_DIR / "index.html"
    if not index_path.is_file():
        return HttpResponse(
            "Фронтенд не собран в этом образе. Локально: cd frontend && npm run dev",
            status=501,
        )
    return HttpResponse(index_path.read_bytes(), content_type="text/html")


# Разрешённые файлы PWA-оболочки в корне "/" — service worker, манифест,
# иконки. Список закрытый: это не общая раздача статики (та идёт через
# whitenoise из /static/frontend/), а узкий обход именно для scope service
# worker'а, которому обязательно быть в корне, а не под префиксом.
FRONTEND_ROOT_FILE_RE = re.compile(
    r"^(sw\.js|registerSW\.js|manifest\.webmanifest|workbox-[\w-]+\.js|"
    r"favicon\.ico|apple-touch-icon\.png|icon-\d+\.png|index\.html)$"
)

_ROOT_FILE_CONTENT_TYPES = {
    ".js": "text/javascript",
    ".webmanifest": "application/manifest+json",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".html": "text/html",
}


def frontend_root_file(request, filename: str):
    """Отдаёт service worker, манифест PWA и иконки из корня "/".

    Обычные ассеты бандла (JS/CSS) идут через whitenoise из
    /static/frontend/ — у них хэш в имени, там и положено. А service worker
    обязан жить в корне: его scope — каталог, где лежит сам файл, и отдать
    его из-под /static/frontend/ значило бы, что офлайн-режим работает
    только под этим префиксом, а не для всего SPA (см. STATICFILES_DIRS
    в settings/base.py и frontend_index выше).

    index.html здесь — не альтернативный маршрут SPA (тот один, "/"), а
    техническая необходимость: precache-манифест service worker'а (vite.config.ts,
    manifestTransforms) ссылается на него относительным путём "index.html",
    который резолвится от адреса самого sw.js, то есть в "/index.html".
    Содержимое то же самое, что отдаёт frontend_index на "/".
    """
    if not FRONTEND_ROOT_FILE_RE.match(filename):
        raise Http404
    file_path = settings.FRONTEND_DIST_DIR / filename
    if not file_path.is_file():
        raise Http404
    content_type = _ROOT_FILE_CONTENT_TYPES.get(file_path.suffix, "application/octet-stream")
    return HttpResponse(file_path.read_bytes(), content_type=content_type)
