"""Подсказка адресов DaData — структурированный адрес вместо свободного текста.

Заявитель выбирает готовый вариант с индексом, район приходит в ответе сам:
это и даёт роутинг заявок по району без геокодирования (оно только с этапа 4).
Тот же приём уже применён к реестру ФИФ — см. apps.arshin.fif, здесь по образцу.

Без ключа (DADATA_API_KEY пуст) подсказка просто не работает: форма на сайте
показывает обычное текстовое поле адреса, is_configured об этом сообщает
заранее, отдельной ошибки на каждый запрос это не производит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SUGGEST_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"


@dataclass
class AddressSuggestion:
    value: str
    postal_code: str = ""
    fias_id: str = ""
    district: str = ""  # city_district_with_type / area_with_type — первое непустое
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class AddressSuggestUnavailable(RuntimeError):
    """DaData не ответила — заявитель вводит адрес вручную, это не ошибка операции."""


class DaDataClient:
    def __init__(self, api_key: str | None = None, timeout: float = 5.0) -> None:
        self.api_key = api_key if api_key is not None else getattr(settings, "DADATA_API_KEY", "")
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def suggest(self, query: str, *, limit: int = 8) -> list[AddressSuggestion]:
        query = (query or "").strip()
        if not self.is_configured or len(query) < 3:
            return []

        try:
            response = requests.post(
                SUGGEST_URL,
                json={"query": query, "count": limit, "locations": [{"region": "Кировская"}]},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Token {self.api_key}",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise AddressSuggestUnavailable(f"Подсказка адресов недоступна: {exc}") from exc
        except ValueError as exc:
            raise AddressSuggestUnavailable("Подсказка адресов вернула не JSON") from exc

        return [self._parse(item) for item in payload.get("suggestions", []) if isinstance(item, dict)]

    @staticmethod
    def _parse(item: dict) -> AddressSuggestion:
        data = item.get("data") or {}
        district = data.get("city_district_with_type") or data.get("area_with_type") or ""
        lat, lon = data.get("geo_lat"), data.get("geo_lon")
        return AddressSuggestion(
            value=item.get("value") or "",
            postal_code=data.get("postal_code") or "",
            fias_id=data.get("fias_id") or "",
            district=district,
            city=data.get("city") or data.get("settlement") or "",
            latitude=float(lat) if lat else None,
            longitude=float(lon) if lon else None,
            raw=item,
        )
