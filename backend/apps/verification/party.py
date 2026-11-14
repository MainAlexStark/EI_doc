"""Организация заказчика — чтобы не набирать её руками.

Два входа:

* ``suggest(q)`` — человек набирает примерное название (или начало ИНН),
  получает список: сначала своя база (заказчик уже сдавал приборы), потом
  DaData «Подсказки по организациям» с приоритетом Кировской области.
  Выбрал строку — ИНН, КПП, ОГРН и адрес подставились.
* ``lookup(inn)`` — точный поиск по полному ИНН (своя база → DaData findById).

Ключ тот же ``DADATA_API_KEY``, что у подсказки адресов. Без ключа — только
своя база, остальное человек вписывает сам.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import requests
from django.conf import settings

from apps.verification.models import Client, ClientKind

logger = logging.getLogger(__name__)

FIND_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
SUGGEST_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party"
# Кировская область (КЛАДР 43) — свои заказчики в выдаче выше, но не только они.
LOCATIONS_BOOST = [{"kladr_id": "43"}]


@dataclass
class Party:
    inn: str
    name: str
    kpp: str = ""
    ogrn: str = ""
    address: str = ""
    phone: str = ""
    client_id: int | None = None
    source: str = "own"  # own | dadata
    status: str = ""  # ACTIVE | LIQUIDATED … — из DaData

    def as_dict(self) -> dict:
        return asdict(self)


def valid_inn(inn: str) -> bool:
    return inn.isdigit() and len(inn) in (10, 12)


def _dadata(inn: str) -> Party | None:
    key = getattr(settings, "DADATA_API_KEY", "")
    if not key:
        return None
    try:
        response = requests.post(
            FIND_URL, json={"query": inn, "count": 1},
            headers={"Authorization": f"Token {key}", "Accept": "application/json"}, timeout=5,
        )
        response.raise_for_status()
        found = response.json().get("suggestions") or []
    except (requests.RequestException, ValueError) as exc:
        logger.info("party: DaData недоступна: %s", exc)
        return None
    if not found:
        return None
    return _from_dadata(found[0], inn)


def _from_dadata(item: dict, inn: str = "") -> Party:
    data = item.get("data") or {}
    name = (data.get("name") or {}).get("short_with_opf") or item.get("value") or ""
    return Party(
        inn=data.get("inn") or inn,
        name=name,
        kpp=data.get("kpp") or "",
        ogrn=data.get("ogrn") or "",
        address=((data.get("address") or {}).get("value")) or "",
        source="dadata",
        status=((data.get("state") or {}).get("status")) or "",
    )


def _own(client: Client) -> Party:
    return Party(inn=client.inn, name=client.name, kpp=client.kpp, ogrn=client.ogrn, address=client.legal_address,
                 phone=client.phone, client_id=client.pk, source="own")


def _dadata_suggest(query: str, count: int) -> list[Party]:
    key = getattr(settings, "DADATA_API_KEY", "")
    if not key or count <= 0:
        return []
    try:
        response = requests.post(
            SUGGEST_URL, json={"query": query, "count": count, "locations_boost": LOCATIONS_BOOST},
            headers={"Authorization": f"Token {key}", "Accept": "application/json"}, timeout=5,
        )
        response.raise_for_status()
        found = response.json().get("suggestions") or []
    except (requests.RequestException, ValueError) as exc:
        logger.info("party: DaData недоступна: %s", exc)
        return []
    return [_from_dadata(item) for item in found]


def suggest(query: str, limit: int = 8) -> list[Party]:
    """Организации под примерное название или начало ИНН: своя база, потом DaData."""
    query = " ".join((query or "").split())
    if len(query) < 2:
        return []
    digits = "".join(ch for ch in query if ch.isdigit())
    own_qs = Client.objects.exclude(kind=ClientKind.PERSON, inn="")
    if digits and len(digits) == len(query.replace(" ", "")):
        own_qs = own_qs.filter(inn__startswith=digits)
    else:
        for word in query.split():
            own_qs = own_qs.filter(name__icontains=word.strip("«»\"'"))
    results: list[Party] = []
    seen: set[str] = set()
    for client in own_qs.order_by("-created_at")[: limit * 2]:
        mark = client.inn or client.name.lower()
        if mark in seen:
            continue
        seen.add(mark)
        results.append(_own(client))
        if len(results) >= max(3, limit // 2):
            break
    own_by_inn = {p.inn: p for p in results if p.inn}
    for found in _dadata_suggest(query, limit):
        if found.inn in own_by_inn:
            # Своя запись без адреса — дополняем реквизитами из DaData.
            mine = own_by_inn[found.inn]
            mine.address = mine.address or found.address
            mine.kpp = mine.kpp or found.kpp
            mine.ogrn = mine.ogrn or found.ogrn
            mine.status = found.status
            continue
        if found.inn and found.inn in seen:
            continue
        seen.add(found.inn)
        results.append(found)
        if len(results) >= limit:
            break
    return results[:limit]


def lookup(inn: str) -> Party | None:
    inn = "".join(ch for ch in (inn or "") if ch.isdigit())
    if not valid_inn(inn):
        return None
    own = Client.objects.filter(inn=inn).order_by("-created_at").first()
    if own and own.legal_address:
        return _own(own)
    found = _dadata(inn)
    if found and own:
        found.client_id = own.pk
        found.phone = own.phone
    if found:
        return found
    if own:
        return _own(own)
    return None
