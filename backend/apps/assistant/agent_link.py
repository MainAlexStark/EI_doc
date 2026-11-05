"""Вкладка «Ассистент» на agentkit: адрес чата агента и токен входа для фрейма.

Сотрудник уже вошёл в EI_doc — второй логин ему не нужен. EI_doc подписывает
короткий токен «это пользователь 12, роли …, до 18:00» секретом, общим с агентом
(``AGENT_LOGIN_SECRET`` здесь = ``AGENT_HOST_LOGIN_SECRET`` у агента), а фронтенд
передаёт его чату во фрейме postMessage'ем. Формат — ``agentkit.server.auth.host_token``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

TTL = 12 * 3600
# Руководство настраивает агента (модели, серверы, лимиты) — в agentkit это роль admin.
ADMIN_ROLES = {"owner", "head"}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def host_token(secret: str, user_id: str, *, name: str, roles: list[str], ttl: int = TTL) -> str:
    body = _b64(json.dumps({"sub": str(user_id), "name": name, "roles": roles, "exp": int(time.time() + ttl)},
                           ensure_ascii=False).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"akh1.{body}.{sig}"


def agent_roles(user) -> list[str]:
    roles = [user.role] if user.role else []
    if user.is_superuser or user.role in ADMIN_ROLES:
        roles.insert(0, "admin")
    return roles


def enabled() -> bool:
    return bool(settings.AGENT_URL and settings.AGENT_LOGIN_SECRET)


class AgentLinkView(APIView):
    """GET → {enabled, url, origin, token, ttl}. Без AGENT_URL — enabled=false: работает старый чат."""

    def get(self, request):
        if not enabled():
            return Response({"enabled": False})
        user = request.user
        employee = getattr(user, "employee", None)
        name = employee.full_name if employee else (user.get_full_name() or user.email or str(user.pk))
        url = settings.AGENT_URL.rstrip("/")
        origin = "/".join(url.split("/")[:3])
        token = host_token(settings.AGENT_LOGIN_SECRET, str(user.pk), name=name, roles=agent_roles(user))
        return Response({"enabled": True, "url": url + "/", "origin": origin, "token": token, "ttl": TTL})
