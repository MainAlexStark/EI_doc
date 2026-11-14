"""Организация по ИНН — чтобы заказчика не набирать руками.

Сначала своя база (заказчик уже сдавал приборы), потом DaData «Организация по
ИНН» (тот же ``DADATA_API_KEY``, что у подсказки адресов). Без ключа —
только своя база, остальное человек вписывает сам.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import requests
from django.conf import settings

from apps.verification.models import Client

logger = logging.getLogger(__name__)

FIND_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


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
    item = found[0]
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


def lookup(inn: str) -> Party | None:
    inn = "".join(ch for ch in (inn or "") if ch.isdigit())
    if not valid_inn(inn):
        return None
    own = Client.objects.filter(inn=inn).order_by("-created_at").first()
    if own and own.legal_address:
        return Party(inn=inn, name=own.name, kpp=own.kpp, ogrn=own.ogrn, address=own.legal_address,
                     phone=own.phone, client_id=own.pk, source="own")
    found = _dadata(inn)
    if found and own:
        found.client_id = own.pk
        found.phone = own.phone
    if found:
        return found
    if own:
        return Party(inn=inn, name=own.name, kpp=own.kpp, ogrn=own.ogrn, address=own.legal_address,
                     phone=own.phone, client_id=own.pk, source="own")
    return None
