"""Диагностические вьюхи core.

Отдельный файл, а не что-то в api.py: это не часть домена, читает его
deploy.sh (через curl), не браузер и не клиент API.
"""

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse


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
