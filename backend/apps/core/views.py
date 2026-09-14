"""Диагностические вьюхи core.

Отдельный файл, а не что-то в api.py: это не часть домена, читает его
deploy.sh (через curl), не браузер и не клиент API.
"""

from django.db import DatabaseError, connection
from django.http import JsonResponse


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
