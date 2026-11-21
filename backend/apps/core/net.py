"""Адрес посетителя за прокси — тем же способом, что и анонимные лимиты DRF."""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class _Ident(SimpleRateThrottle):
    rate = "1/s"  # не используется — нужен только get_ident

    def get_cache_key(self, request, view):  # pragma: no cover — не лимит
        return None


def client_ip(request) -> str:
    """IP заявителя: при NUM_PROXIES — из X-Forwarded-For, иначе REMOTE_ADDR."""
    ip = _Ident().get_ident(request) or ""
    return ip.split(",")[0].strip()
