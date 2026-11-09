"""Госреестр через MCP-сервер РСТ (RSTMetrology_MCP), а не напрямую.

ФГИС «Аршин» отвечает только российским адресам, а EI_doc может стоять где угодно (разработка у
Алекса, сервер за рубежом, VPN). MCP-сервер РСТ живёт на российском VPS и ходит в ФГИС сам, поэтому
он — основной путь, если задан ``RST_MCP_URL``; прямой запрос остаётся запасным
(``apps.arshin.fif.FifClient``).

Инструменты сервера, которые нужны здесь:

* ``search_types {search, rows}`` → текст-JSON ``{"count", "items": [{mit_uuid, number, title,
  notation, manufacturers}], …}`` — ровно поля прямого API ``/mit``;
* ``get_type {id}`` (mit_uuid или номер в реестре) → ``{"card": {"general", "mpi", "meth", "spec",
  "manufacturer", …}}`` — разбирается тем же ``FifClient.parse_card``.

Сессия заводится на каждый вызов: так проще и надёжнее между процессами. Обрыв ответа
(«Response ended prematurely» на длинных ответах) повторяется один раз.
"""

from __future__ import annotations

import json
from typing import Any

from django.conf import settings

TIMEOUT = 25.0


class RstMcpError(RuntimeError):
    """MCP-сервер РСТ недоступен или ответил ошибкой — текст для человека."""


def configured() -> bool:
    return bool(getattr(settings, "RST_MCP_URL", ""))


def _headers() -> dict:
    token = getattr(settings, "RST_MCP_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _session(timeout: float):
    from apps.assistant.mcp_client import McpSession

    return McpSession(settings.RST_MCP_URL, _headers(), timeout=timeout)


def call(tool: str, arguments: dict, *, timeout: float = TIMEOUT) -> Any:
    """Вызвать инструмент и вернуть разобранный JSON его текстового ответа."""
    import requests

    from apps.assistant.mcp_client import McpError

    if not configured():
        raise RstMcpError("MCP-сервер РСТ не настроен (RST_MCP_URL)")
    result, last = None, None
    for _attempt in range(2):
        try:
            with _session(timeout) as session:
                result = session.call_tool(tool, arguments)
            break
        except (McpError, requests.RequestException) as exc:
            last = exc
            if "отказал в доступе" in str(exc):  # токен — повтор не поможет
                break
    if result is None:
        raise RstMcpError(f"MCP-сервер РСТ: {last}")

    text = "".join(
        part.get("text", "") for part in (result.get("content") or []) if isinstance(part, dict)
    ).strip()
    if result.get("isError"):
        raise RstMcpError(f"MCP-сервер РСТ: {text[:300] or 'ошибка без текста'}")
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    try:
        return json.loads(text)
    except ValueError as exc:
        raise RstMcpError(f"MCP-сервер РСТ ответил не JSON: {text[:200]}") from exc
