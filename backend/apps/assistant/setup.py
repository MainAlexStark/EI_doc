"""RSTMetrology_MCP подключается сам, если его адрес задан в .env.

RST_MCP_URL — адрес /mcp сервера (на VPS — внутри mcp-network по имени
контейнера, без выхода наружу); RST_MCP_TOKEN — токен доступа, если сервер
его требует. Дальше сервер правится на экране «Контекст → Инструменты».
"""

from __future__ import annotations

from django.conf import settings


def ensure_rst_server(**kwargs) -> None:
    from apps.assistant.models import McpServer

    url = getattr(settings, "RST_MCP_URL", "")
    if not url:
        return
    token = getattr(settings, "RST_MCP_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    server, created = McpServer.objects.get_or_create(
        name="rst",
        defaults={
            "title": "RSTMetrology (ФГИС «Аршин»)",
            "url": url,
            "headers": headers,
            "description": "Реестр утверждённых типов СИ и результаты поверок ФГИС «Аршин»",
        },
    )
    if not created and (server.url != url or (token and server.headers != headers)):
        server.url = url
        if token:
            server.headers = headers
        server.save(update_fields=["url", "headers"])
