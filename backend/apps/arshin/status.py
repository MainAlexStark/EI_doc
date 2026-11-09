"""Связь с Госреестром (РСТ): какие пути живы — для индикатора на экране.

Каждый настоящий запрос ``FifClient`` отмечает здесь свой исход (``record``), поэтому индикатор
обычно ничего не спрашивает у ФГИС сам. Проба (``probe``) — один дешёвый поиск по каждому пути —
идёт, только когда отметок давно не было или человек нажал «Проверить».

Кэш может быть недоступен (Redis лёг, локальный запуск без Redis) — тогда статус просто не
запоминается, а поиск продолжает работать.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)

ROUTES = {
    "mcp": "MCP-сервер РСТ",
    "direct": "ФГИС напрямую",
}
STATUS_TTL = 60 * 60
FRESH = 120  # с — свежая отметка не требует пробы
PROBE_QUERY = "16078-05"  # номер существующего типа: короткий ответ, есть всегда

# Последние отметки в памяти процесса — на случай, если кэша нет (локально без Redis).
_local: dict[str, dict] = {}


def cache_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except Exception as exc:  # noqa: BLE001 — кэш не должен ронять работу с реестром
        log.warning("Кэш недоступен (%s): %s", key, exc)
        return default


def cache_set(key: str, value, ttl: int) -> None:
    try:
        cache.set(key, value, ttl)
    except Exception as exc:  # noqa: BLE001
        log.warning("Кэш недоступен (%s): %s", key, exc)


def mode() -> str:
    """auto — MCP, если настроен, затем напрямую; mcp / direct — только этот путь."""
    value = str(getattr(settings, "FIF_TRANSPORT", "auto") or "auto").lower()
    return value if value in ("auto", "mcp", "direct") else "auto"


def routes() -> list[str]:
    from apps.arshin import rst_mcp

    chosen = mode()
    if chosen == "mcp":
        return ["mcp"]
    if chosen == "direct":
        return ["direct"]
    return ["mcp", "direct"] if rst_mcp.configured() else ["direct"]


def record(route: str, ok: bool, *, ms: int | None = None, error: str = "") -> dict:
    state = {"ok": ok, "ms": ms, "error": error[:300], "checked_at": timezone.now().isoformat(), "at": time.time()}
    _local[route] = state
    cache_set(f"fif:status:{route}", state, STATUS_TTL)
    return state


def last(route: str) -> dict | None:
    """Последняя отметка пути: из общего кэша (видят все процессы), иначе из памяти процесса."""
    shared = cache_get(f"fif:status:{route}")
    mine = _local.get(route)
    if shared and mine:
        return shared if shared.get("at", 0) >= mine.get("at", 0) else mine
    return shared or mine


def _probe(route: str) -> None:
    from apps.arshin.fif import FifClient, FifUnavailable

    client = FifClient(timeout=4.0)
    started = time.monotonic()
    try:
        client.search_on(route, PROBE_QUERY, rows=1)
    except FifUnavailable:
        return  # исход уже записан search_on
    except Exception as exc:  # noqa: BLE001 — проба не должна падать
        record(route, False, ms=int((time.monotonic() - started) * 1000), error=str(exc))


def snapshot(*, probe: bool = False) -> dict:
    """{ok, via, mode, routes: [{key, label, ok, ms, error, checked_at}]} — что показать на экране."""
    active = routes()
    rows = []
    for route in active:
        state = last(route)
        stale = state is None or time.time() - state.get("at", 0) > FRESH
        # Запасной путь сам не проверяем, пока основной на связи: на зарубежном сервере прямой
        # запрос к ФГИС — это секунды ожидания таймаута на каждый показ индикатора.
        backup_not_needed = any(r["ok"] for r in rows)
        if probe or (stale and not backup_not_needed):
            _probe(route)
            state = last(route)
        state = state or {"ok": None, "ms": None, "error": "", "checked_at": None}
        rows.append({"key": route, "label": ROUTES[route], **{k: state.get(k) for k in ("ok", "ms", "error",
                                                                                          "checked_at")}})
    via = next((r["key"] for r in rows if r["ok"]), None)
    return {"ok": via is not None, "via": via, "via_label": ROUTES.get(via, ""), "mode": mode(), "routes": rows}
