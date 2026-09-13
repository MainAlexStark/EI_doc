"""Клиент открытого реестра ФИФ ОЕИ (ФГИС «Аршин»).

Задача, ради которой это написано: при заведении СИ поверитель вводит номер в
Госреестре или часть названия, а система показывает готовые варианты
**с изготовителем**. Счётчики с одинаковым названием от разных производителей
— частая причина ошибок, и выбор из списка эту проблему снимает.

ВАЖНО про схему ответа. Открытый API Аршина не документирован публично и
менялся, а из среды разработки он недоступен. Поэтому разбор ответа здесь
намеренно терпимый: поля ищутся по нескольким вероятным именам, неизвестные
сохраняются в `raw`. Перед первым боевым запуском выполните на сервере

    python manage.py fif_probe --query СВК

— команда покажет фактический ответ, после чего `FIELD_ALIASES` ниже
уточняется под него одной правкой.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://fgis.gost.ru/fundmetrology/eapi"
CACHE_TTL = 60 * 60 * 12

# Имя нашего поля -> вероятные имена в ответе ФИФ, по порядку предпочтения.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "registry_number": ("number", "reg_number", "regNumber", "num_mit", "registry_number"),
    "name": ("title", "name", "mit_name", "nameSi"),
    "manufacturer": ("manufacturer", "manufactorer", "producer", "manufacturerName", "izg"),
    "notation": ("notation", "designation", "mit_notation", "type"),
    "interval_months": ("mpi", "interval", "verification_interval"),
    "uuid": ("id", "uuid", "rowId"),
}


@dataclass
class FifType:
    """Кандидат типа СИ из Госреестра."""

    registry_number: str = ""
    name: str = ""
    manufacturer: str = ""
    notation: str = ""
    interval_months: int | None = None
    uuid: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Строка для списка подсказок — изготовитель обязателен."""
        parts = [self.name or self.notation or "без наименования"]
        if self.registry_number:
            parts.append(f"№ {self.registry_number}")
        parts.append(self.manufacturer or "изготовитель не указан")
        return " · ".join(parts)


class FifUnavailable(RuntimeError):
    """Реестр не ответил. Заведение СИ должно продолжаться вручную."""


class FifClient:
    def __init__(self, base_url: str | None = None, timeout: float = 8.0) -> None:
        self.base_url = (base_url or getattr(settings, "ARSHIN_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout

    # -- низкий уровень -------------------------------------------------
    def _get(self, path: str, params: dict) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise FifUnavailable(f"Реестр ФИФ недоступен: {exc}") from exc
        except ValueError as exc:
            raise FifUnavailable("Реестр ФИФ вернул не JSON") from exc

    @staticmethod
    def _rows(payload: Any) -> Iterable[dict]:
        """Вытащить строки из ответа, какой бы обёртки он ни был."""
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("result", "results", "items", "rows", "data", "response"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
            if isinstance(nested, dict):
                return FifClient._rows(nested)
        return []

    @staticmethod
    def _pick(row: dict, aliases: tuple[str, ...]) -> Any:
        lowered = {str(key).lower(): value for key, value in row.items()}
        for alias in aliases:
            value = lowered.get(alias.lower())
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _parse(cls, row: dict) -> FifType:
        values: dict[str, Any] = {}
        for our_name, aliases in FIELD_ALIASES.items():
            values[our_name] = cls._pick(row, aliases)

        interval = values.get("interval_months")
        try:
            interval = int(str(interval).strip()) if interval is not None else None
        except (TypeError, ValueError):
            interval = None

        return FifType(
            registry_number=str(values.get("registry_number") or "").strip(),
            name=str(values.get("name") or "").strip(),
            manufacturer=str(values.get("manufacturer") or "").strip(),
            notation=str(values.get("notation") or "").strip(),
            interval_months=interval,
            uuid=str(values.get("uuid") or "").strip(),
            raw=row,
        )

    # -- публичный уровень ----------------------------------------------
    def search_types(self, query: str, *, limit: int = 20) -> list[FifType]:
        """Поиск типов СИ по номеру в Госреестре или части наименования."""
        query = (query or "").strip()
        if len(query) < 2:
            return []

        key = f"fif:types:{query.lower()}:{limit}"
        cached = cache.get(key)
        if cached is not None:
            return [FifType(**item) for item in cached]

        payload = self._get("mit", {"search": query, "rows": limit, "start": 0})
        found = [self._parse(row) for row in self._rows(payload)][:limit]

        cache.set(key, [item.__dict__ for item in found], CACHE_TTL)
        return found

    def raw_search(self, query: str, *, limit: int = 5) -> Any:
        """Сырой ответ — для команды fif_probe."""
        return self._get("mit", {"search": query, "rows": limit, "start": 0})


def mark_ambiguous(candidates: list[FifType]) -> list[dict]:
    """Пометить кандидатов, у которых совпадает наименование.

    Ровно тот случай, ради которого всё затевалось: один и тот же «СВК-15»
    от трёх изготовителей. Такие варианты нельзя выбирать вслепую —
    интерфейс обязан показать изготовителя и потребовать явного выбора.
    """
    counts: dict[str, int] = {}
    for candidate in candidates:
        normalized = candidate.name.strip().lower()
        counts[normalized] = counts.get(normalized, 0) + 1

    return [
        {
            "registry_number": candidate.registry_number,
            "name": candidate.name,
            "manufacturer": candidate.manufacturer,
            "notation": candidate.notation,
            "interval_months": candidate.interval_months,
            "label": candidate.label,
            "ambiguous": counts.get(candidate.name.strip().lower(), 0) > 1,
        }
        for candidate in candidates
    ]
