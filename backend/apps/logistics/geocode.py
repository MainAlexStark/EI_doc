"""Координаты адреса — чтобы поставить точку на карте командировки.

Главный источник координат — не этот модуль, а телефон поверителя («Я здесь»
на форме адреса) и подсказка DaData, из которой адрес выбран: оба дают точку
сразу. Сюда попадают адреса, набранные текстом (план накануне, список строками).

Порядок: DaData (если есть ключ — тот же, что у формы заявки), затем Nominatim
(OpenStreetMap, без ключа, не чаще запроса в секунду). Nominatim по сёлам
знает далеко не каждый дом и отвечает осторожно (см. ``_nominatim``), так что
без ключа DaData заметная часть плановых адресов останется без точки. Это не
ошибка: в списке адрес есть, а точку поставит телефон, когда до него доедут.

``GEOCODER``: ``auto`` (по умолчанию) | ``dadata`` | ``nominatim`` | ``off``.
"""

from __future__ import annotations

import logging
import re

import requests
from django.conf import settings

from apps.hub.address import AddressSuggestUnavailable, DaDataClient

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "EI_doc/1.0 (trip planner; eicounters.ru)"

Point = tuple[float, float]


def mode() -> str:
    return getattr(settings, "GEOCODER", "auto")


def is_enabled() -> bool:
    return mode() != "off"


def _region() -> str:
    return getattr(settings, "GEOCODER_REGION", "Кировская область")


def _dadata(address: str) -> Point | None:
    client = DaDataClient(timeout=4.0)
    if not client.is_configured:
        return None
    try:
        found = client.suggest(address, limit=1)
    except AddressSuggestUnavailable as exc:
        logger.info("geocode: DaData недоступна: %s", exc)
        return None
    for item in found:
        if item.latitude is not None and item.longitude is not None:
            return item.latitude, item.longitude
    return None


_APARTMENT = re.compile(r",?\s*\b(кв|квартира|оф|офис|пом|помещение|ком|комната)\.?\s*№?\s*[\w/-]+\s*$", re.IGNORECASE)
_PLACE_PREFIX = re.compile(
    r"^(г|гор|город|д|дер|деревня|с|село|п|пос|поселок|посёлок|пгт|рп|ст|станция|слобода|сл)\.?\s+", re.IGNORECASE
)
_STREET_START = re.compile(
    r"^(ул|улица|пер|переулок|пр|пр-кт|просп|проспект|проезд|пр-д|б-р|бульвар|наб|набережная|ш|шоссе|тракт|пл|площадь|мкр|микрорайон)\b",
    re.IGNORECASE,
)
_REGION_PART = re.compile(r"(область|обл\.?|край|республика|респ\.?|район|р-н)\b", re.IGNORECASE)
_HOUSE_PREFIX = re.compile(r"^(д|дом)\.?\s*(?=\d)", re.IGNORECASE)


def split_address(address: str, hint: str = "") -> tuple[str, str]:
    """Адрес → (населённый пункт, остальное без квартиры). Пункт пуст — не разобрали.

    Понимает и запись подсказки («г Слободской, ул Советская, д 10, кв 5»), и то,
    как пишут от руки («Слободской, Советская 10-5» разберёт только до дома).
    Если адрес начинается сразу с улицы, пунктом считается ``hint`` — куда едет командировка.
    """
    text = _APARTMENT.sub("", " ".join(address.split()))
    parts = [part.strip() for part in text.split(",") if part.strip()]
    parts = [part for part in parts if not _REGION_PART.search(part)]
    if not parts:
        return "", ""
    if _STREET_START.match(parts[0]):
        place = _PLACE_PREFIX.sub("", hint.strip()) if hint and not _REGION_PART.search(hint) else ""
        rest = parts
    else:
        place, rest = _PLACE_PREFIX.sub("", parts[0]), parts[1:]
    rest = [_HOUSE_PREFIX.sub("", part) for part in rest]
    return place.strip(), ", ".join(rest)


def _nominatim(address: str, hint: str = "") -> Point | None:
    """Точка от OpenStreetMap — только если ответ лежит в том же населённом пункте.

    Свободный поиск по сельским адресам охотно отдаёт одноимённую улицу соседней
    деревни (в «Слободской, ул Советская, 10» находится Бобино Слободского района).
    Точка не там хуже, чем никакой: поверитель поедет по ней. Поэтому ответ
    принимается, только когда среди его частей есть сам населённый пункт,
    а не район с тем же названием.
    """
    place, rest = split_address(address, hint)
    if not place or not rest:
        return None
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": ", ".join(filter(None, [_region(), place, rest])),
                "format": "jsonv2", "limit": 5, "countrycodes": "ru", "accept-language": "ru",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=5.0,
        )
        response.raise_for_status()
        found = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.info("geocode: Nominatim недоступен: %s", exc)
        return None
    wanted = place.casefold().replace("ё", "е")
    for item in found if isinstance(found, list) else []:
        parts = [part.strip().casefold().replace("ё", "е") for part in str(item.get("display_name", "")).split(",")]
        if wanted not in parts:
            continue
        try:
            return float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def geocode(address: str, hint: str = "") -> Point | None:
    """Точка по адресу или None. Никогда не бросает — карта не должна ронять сохранение адреса.

    ``hint`` — куда едет командировка: помогает, когда адрес записан без населённого пункта.
    """
    address = (address or "").strip()
    current = mode()
    if current == "off" or len(address) < 5:
        return None
    if current in ("auto", "dadata"):
        point = _dadata(address)
        if point or current == "dadata":
            return point
    return _nominatim(address, hint)
