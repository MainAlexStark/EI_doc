from .base import *  # noqa: F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

# X-Accel-Redirect был завязан на nginx, которого в проде больше нет —
# см. SENDFILE_* в base.py. Здесь специально ничего не переопределяется:
# прод и dev отдают защищённые файлы одинаково.

# Перед gunicorn ровно один прокси — общий Caddy (см. docker-compose.yml): по нему
# DRF берёт адрес посетителя из X-Forwarded-For для анонимных лимитов.
REST_FRAMEWORK = {**REST_FRAMEWORK, "NUM_PROXIES": env.int("NUM_PROXIES", default=1)}  # noqa: F405
