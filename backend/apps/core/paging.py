"""Списки без сюрпризов под нагрузкой.

Экраны сейчас ждут массив, поэтому по умолчанию список остаётся массивом —
но всегда с потолком (``limit``, по умолчанию 200, не больше 1000). Кто хочет
страницы, просит их явно: ``?page_size=50&page=2`` — тогда ответ
``{"count", "page", "page_size", "next_page", "results"}``.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from rest_framework.response import Response

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
MAX_PAGE_SIZE = 200


def _int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def respond(request, queryset, serialize: Callable[[list], Iterable[Any]], *, default_limit: int = DEFAULT_LIMIT) -> Response:
    """Массив (с потолком) или страница — смотря что спросили."""
    params = request.query_params
    if "page_size" in params or "page" in params:
        size = _int(params.get("page_size"), 50, 1, MAX_PAGE_SIZE)
        page = _int(params.get("page"), 1, 1, 10**6)
        count = queryset.count()
        rows = list(queryset[(page - 1) * size: page * size])
        return Response({
            "count": count, "page": page, "page_size": size,
            "next_page": page + 1 if page * size < count else None,
            "results": list(serialize(rows)),
        })
    limit = _int(params.get("limit"), default_limit, 1, MAX_LIMIT)
    return Response(list(serialize(list(queryset[:limit]))))
