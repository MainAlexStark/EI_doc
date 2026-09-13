from .base import *  # noqa: F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

# Файлы отдаёт nginx: Django проверяет права и возвращает заголовок,
# сам файл по сети из Python не течёт.
USE_X_ACCEL_REDIRECT = True
X_ACCEL_MEDIA_PREFIX = "/protected-media/"
