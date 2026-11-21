"""Настройки gunicorn — из окружения, чтобы масштабировать без пересборки образа.

    GUNICORN_WORKERS   процессов (по умолчанию 2 × ядер + 1, не больше 8)
    GUNICORN_THREADS   потоков в процессе (по умолчанию 4): запросы к DaData/ФГИС/2ГИС
                       ждут сеть, потоки не дают им занять весь процесс
    GUNICORN_TIMEOUT   секунд на запрос (по умолчанию 120 — вёрстка протоколов, ФГИС)
    GUNICORN_MAX_REQUESTS  перезапуск процесса после N запросов (утечки памяти OR-Tools/PDF)

Запуск: gunicorn -c config/gunicorn.py config.wsgi:application
"""

import multiprocessing
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = _int("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 8))
threads = _int("GUNICORN_THREADS", 4)
worker_class = "gthread" if threads > 1 else "sync"
timeout = _int("GUNICORN_TIMEOUT", 120)
graceful_timeout = 30
keepalive = 5
max_requests = _int("GUNICORN_MAX_REQUESTS", 2000)
max_requests_jitter = max_requests // 10 if max_requests else 0
accesslog = "-" if os.environ.get("GUNICORN_ACCESS_LOG", "") == "1" else None
errorlog = "-"
# Код приложения грузится в мастере один раз — процессы стартуют быстрее и делят память.
preload_app = os.environ.get("GUNICORN_PRELOAD", "0") == "1"
