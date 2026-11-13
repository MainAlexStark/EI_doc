"""Какие типы СИ подходят прибору по году выпуска.

Номер в Госреестре — «16078-13»: до дефиса номер типа, после — год, когда тип внесли в реестр.
Один и тот же счётчик со временем переутверждают: у «Бетар СГВ-15» это 16078-97 → 16078-05 →
16078-13 (номер тот же, год новый; чаще меняется и сам номер). Прибор выпущен по той записи,
что действовала в год выпуска, поэтому для счётчика 2020 года:

* 16078-22 предлагать нельзя — типа тогда ещё не было в реестре;
* 16078-05 предлагать незачем — его в 2013-м сменил 16078-13.

Если прибор выпущен в год переутверждения (2013-й), остаются обе записи — 16078-05 и
16078-13: по номеру не узнать, до или после новой записи его сделали.
"""

from __future__ import annotations

import re
from datetime import date

_NUMBER = re.compile(r"^\s*(\d+)\s*-\s*(\d{2})\s*$")
MIN_YEAR = 1950


def parse_year(raw: str | int | None) -> int | None:
    """Год выпуска из запроса: четыре цифры в разумных пределах, иначе — «год не указан»."""
    try:
        year = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return year if MIN_YEAR <= year <= date.today().year + 1 else None


def split_number(registry_number: str) -> tuple[str, int] | None:
    """«16078-13» → («16078», 2013). Номера не такого вида (старые, ручные) — None."""
    match = _NUMBER.match(registry_number or "")
    if not match:
        return None
    short = int(match.group(2))
    # «97» — это 1997-й, «05» — 2005-й: реестр ведётся с 90-х, записей из будущего в нём нет.
    century = 2000 if short <= date.today().year % 100 else 1900
    return match.group(1).lstrip("0") or "0", century + short


def fits_year(items: list[dict], year: int | None) -> list[dict]:
    """Оставить типы, по которым прибор мог быть выпущен в ``year``. Порядок сохраняется.

    Типы без разборчивого номера остаются всегда: судить о них не по чему.
    """
    if not year:
        return items

    parsed = [split_number(item.get("registry_number", "")) for item in items]
    # Последняя запись каждого номера из тех, что уже были в реестре к году выпуска.
    latest: dict[str, int] = {}
    for number in parsed:
        if number and number[1] <= year:
            latest[number[0]] = max(latest.get(number[0], 0), number[1])

    kept = []
    for item, number in zip(items, parsed):
        if number is None:
            kept.append(item)
            continue
        base, registered = number
        if registered > year:
            continue
        current = latest[base]
        # Запись либо действующая, либо её сменили в самый год выпуска.
        if registered == current or current == year and registered == _previous(parsed, base, current):
            kept.append(item)
    return kept


def _previous(parsed: list[tuple[str, int] | None], base: str, current: int) -> int | None:
    """Год записи того же номера, шедшей прямо перед ``current``."""
    earlier = [number[1] for number in parsed if number and number[0] == base and number[1] < current]
    return max(earlier) if earlier else None
