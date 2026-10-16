"""Минимальный клиент MCP по транспорту Streamable HTTP.

Нужен ровно для двух вещей: узнать список инструментов сервера (tools/list)
и вызвать инструмент (tools/call). Сервер может ответить и обычным JSON, и
потоком SSE — разбираются оба варианта. Сессия (Mcp-Session-Id) заводится
на каждый вызов заново: прогоны агента живут в разных процессах Celery, а
инструменты RSTMetrology_MCP состояния между вызовами не держат.
"""

from __future__ import annotations

import itertools
import json
import logging
import re

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
_ids = itertools.count(1)


class McpError(RuntimeError):
    pass


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def tool_name(server_name: str, tool: str) -> str:
    return f"mcp__{_safe(server_name)}__{_safe(tool)}"[:64]


class McpSession:
    def __init__(self, url: str, headers: dict | None = None, timeout: float = 90) -> None:
        self.url = url
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.timeout = timeout
        self.session_id: str | None = None
        self.http = requests.Session()

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *exc):
        try:
            if self.session_id:
                self.http.delete(self.url, headers=self._headers(), timeout=5)
        except requests.RequestException:
            pass
        self.http.close()

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, payload: dict, expect_reply: bool = True) -> dict | None:
        try:
            response = self.http.post(
                self.url, headers=self._headers(), data=json.dumps(payload), timeout=self.timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise McpError(f"MCP-сервер недоступен: {exc}") from exc
        if response.headers.get("Mcp-Session-Id"):
            self.session_id = response.headers["Mcp-Session-Id"]
        if response.status_code in (401, 403):
            raise McpError(f"MCP-сервер отказал в доступе ({response.status_code}) — проверьте токен в настройках")
        if response.status_code >= 400:
            raise McpError(f"MCP-сервер ответил {response.status_code}: {response.text[:300]}")
        if not expect_reply:
            response.close()
            return None

        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            reply = None
            data_lines: list[str] = []
            for raw in response.iter_lines(decode_unicode=True):
                if raw is None:
                    continue
                if raw.startswith("data:"):
                    data_lines.append(raw[5:].lstrip())
                    continue
                if raw == "" and data_lines:
                    try:
                        message = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        message = None
                    data_lines = []
                    if isinstance(message, dict) and message.get("id") == payload.get("id"):
                        reply = message
                        break
            if reply is None and data_lines:
                try:
                    message = json.loads("\n".join(data_lines))
                    if message.get("id") == payload.get("id"):
                        reply = message
                except json.JSONDecodeError:
                    pass
            response.close()
            if reply is None:
                raise McpError("MCP-сервер не прислал ответ")
        else:
            try:
                reply = response.json()
            except ValueError as exc:
                raise McpError("MCP-сервер ответил не JSON") from exc
        if "error" in reply:
            error = reply["error"]
            raise McpError(f"MCP: {error.get('message', error)}")
        return reply.get("result") or {}

    def request(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": next(_ids), "method": method}
        if params is not None:
            payload["params"] = params
        return self._post(payload) or {}

    def initialize(self) -> dict:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ei-doc-assistant", "version": "1.0"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        return result

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(20):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})


def server_tools(server, *, refresh: bool = False) -> list[dict]:
    """Список инструментов сервера (кэш 10 минут)."""
    key = f"assistant:mcp-tools:{server.pk}:{server.url}"
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached
    with McpSession(server.url, server.headers, timeout=20) as session:
        tools = session.list_tools()
    cache.set(key, tools, 600)
    return tools


def call(server, tool: str, arguments: dict) -> tuple[str, bool]:
    """Вызвать инструмент. Возвращает (текст результата, ошибка ли это)."""
    with McpSession(server.url, server.headers) as session:
        result = session.call_tool(tool, arguments)
    parts: list[str] = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(item.get("text", ""))
        elif kind == "resource":
            resource = item.get("resource") or {}
            parts.append(resource.get("text") or f"[ресурс {resource.get('uri', '')}]")
        else:
            parts.append(f"[{kind}]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return "\n".join(parts), bool(result.get("isError"))
