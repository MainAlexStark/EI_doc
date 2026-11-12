"""Сведения о результатах поверки из ФГИС «Аршин» (раздел «Результаты поверки», ``/vri``).

Зачем: поверитель вводит заводской номер счётчика, а система находит его прошлые поверки и сама
подставляет всё, что из них известно, — тип, модификацию, год выпуска.

Что выяснено на живой ФГИС 10.11.2026 (через MCP-сервер РСТ):

* ``/vri?mi_number=…`` без периода ищет только в текущем году;
* запрос с ``verification_date_start`` за несколько лет не укладывается в таймаут, а запрос за один
  год (``year=2026``) отвечает за секунду. Поэтому история собирается **по годам**, несколькими
  запросами одновременно;
* ``mi_number`` сравнивается целиком (без учёта регистра), ``?`` — любой один знак. На корпусе «С»
  бывает и русской, и латинской, причём под обоими номерами могут числиться разные приборы
  (С2674623 — счётчик воды, C2674623 — манометр). Поэтому похожие буквы уходят в запрос как ``?``,
  а лишнее («Z2674623») отсеивается здесь по ``serial_key``; человеку показываются оба прибора.

Пути те же, что у реестра типов (``apps.arshin.status.routes``): MCP-сервер РСТ
(``search_verifications {mi_number, year, rows}``, ``get_verification {vri_id, include_raw}``),
затем ФГИС напрямую (``GET /vri?mi_number=&year=``, ``GET /vri/<vri_id>``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from django.conf import settings

from apps.arshin import rst_mcp
from apps.arshin import status as fif_status
from apps.arshin.fif import FifClient, FifUnavailable, _date

HISTORY_TTL = 60 * 10
CARD_TTL = 60 * 60 * 24
WORKERS = 8
ROWS = 50

# Буквы, одинаковые на вид в русской и латинской раскладке.
_CYR = "АВЕКМНОРСТУХ"
_LAT = "ABEKMHOPCTYX"
_TO_LAT = str.maketrans(_CYR + _CYR.lower(), _LAT + _LAT.lower())
_TO_CYR = str.maketrans(_LAT + _LAT.lower(), _CYR + _CYR.lower())


def years_back() -> int:
    """За сколько прошлых лет искать. 7 — межповерочный интервал счётчиков воды (6 лет) с запасом."""
    return int(getattr(settings, "FIF_VRI_YEARS_BACK", 7))


def serial_variants(serial: str) -> list[str]:
    """Номер как набран, затем он же с похожими буквами в другой раскладке."""
    serial = (serial or "").strip()
    variants = [serial]
    for other in (serial.translate(_TO_CYR), serial.translate(_TO_LAT)):
        if other and other not in variants:
            variants.append(other)
    return variants


def serial_pattern(serial: str) -> str:
    """Номер для запроса во ФГИС: буквы, одинаковые в двух раскладках, заменены на «?»."""
    return "".join("?" if ch in _CYR + _LAT + _CYR.lower() + _LAT.lower() else ch for ch in serial.strip())


def serial_key(serial: str) -> str:
    """Ключ сравнения номеров: без пробелов и дефисов, одна раскладка, один регистр."""
    return re.sub(r"[\s\-–]+", "", str(serial or "")).translate(_TO_LAT).upper()


def _iso(value) -> str | None:
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


@dataclass
class VriRecord:
    """Одна запись о поверке — строка поиска."""

    vri_id: str = ""
    org_title: str = ""
    mit_number: str = ""
    mit_title: str = ""
    mit_notation: str = ""
    modification: str = ""
    mi_number: str = ""
    verification_date: str | None = None
    valid_date: str | None = None
    docnum: str = ""
    applicable: bool | None = None

    @classmethod
    def parse(cls, row: dict) -> "VriRecord":
        applicable = row.get("applicability")
        return cls(
            vri_id=str(row.get("vri_id") or "").strip(),
            org_title=str(row.get("org_title") or "").strip(),
            mit_number=str(row.get("mit_number") or "").strip(),
            mit_title=str(row.get("mit_title") or "").strip(),
            mit_notation=str(row.get("mit_notation") or "").strip(),
            modification=str(row.get("mi_modification") or "").strip(),
            mi_number=str(row.get("mi_number") or "").strip(),
            verification_date=_iso(row.get("verification_date")),
            valid_date=_iso(row.get("valid_date")),
            docnum=str(row.get("result_docnum") or "").strip(),
            applicable=applicable if isinstance(applicable, bool) else None,
        )


@dataclass
class VriHistory:
    serial: str
    items: list[VriRecord] = field(default_factory=list)
    years: tuple[int, int] = (0, 0)
    failed_years: list[int] = field(default_factory=list)
    error: str = ""

    @property
    def partial(self) -> bool:
        return bool(self.failed_years)

    def as_dict(self) -> dict:
        return {
            "serial": self.serial,
            "items": [asdict(item) for item in self.items],
            "years": list(self.years),
            "failed_years": self.failed_years,
            "partial": self.partial,
            "error": self.error,
        }


@dataclass
class VriCard:
    """Карточка поверки — то, чего нет в строке поиска: год выпуска, владелец, методика, эталоны."""

    vri_id: str
    mit_number: str = ""
    mit_title: str = ""
    mit_notation: str = ""
    mit_uuid: str = ""
    modification: str = ""
    mi_number: str = ""
    manufacture_year: int | None = None
    organization: str = ""
    owner: str = ""
    verification_date: str | None = None
    valid_date: str | None = None
    method: str = ""
    applicable: bool | None = None
    docnum: str = ""
    additional_info: str = ""
    standards: list[dict] = field(default_factory=list)
    card_url: str = ""

    @classmethod
    def parse(cls, payload: Any, vri_id: str) -> "VriCard":
        body = payload
        if isinstance(body, dict) and isinstance(body.get("raw"), dict):  # MCP-сервер РСТ
            body = body["raw"]
        elif isinstance(body, dict) and isinstance(body.get("result"), dict):  # ФГИС напрямую
            body = body["result"]
        if not isinstance(body, dict) or not isinstance(body.get("miInfo"), dict):
            raise FifUnavailable("Карточка поверки пришла в неожиданном формате")

        mi_info = body["miInfo"]
        # singleMI — обычное СИ; etaMI — СИ, применяемое как эталон; partyMI — партия (без номера).
        mi = next((mi_info[k] for k in ("singleMI", "etaMI", "partyMI") if isinstance(mi_info.get(k), dict)), {})
        vri = body.get("vriInfo") if isinstance(body.get("vriInfo"), dict) else {}
        good = vri.get("applicable") if isinstance(vri.get("applicable"), dict) else None
        bad = vri.get("inapplicable") if isinstance(vri.get("inapplicable"), dict) else None
        means = body.get("means") if isinstance(body.get("means"), dict) else {}
        info = body.get("info") if isinstance(body.get("info"), dict) else {}

        try:
            year = int(mi.get("manufactureYear")) if mi.get("manufactureYear") else None
        except (TypeError, ValueError):
            year = None
        modification = str(mi.get("modification") or "").strip()
        if modification.lower() in ("нет модификации", "нет", "-", "отсутствует"):
            modification = ""

        standards = [
            {
                "reg_number": str(row.get("regNumber") or ""),
                "mit_number": str(row.get("mitypeNumber") or ""),
                "title": str(row.get("mitypeTitle") or ""),
                "notation": str(row.get("notation") or ""),
                "serial_number": str(row.get("manufactureNum") or ""),
                "rank": str(row.get("rankTitle") or ""),
            }
            for row in means.get("mieta") or [] if isinstance(row, dict)
        ]
        return cls(
            vri_id=vri_id,
            mit_number=str(mi.get("mitypeNumber") or "").strip(),
            mit_title=str(mi.get("mitypeTitle") or "").strip(),
            mit_notation=str(mi.get("mitypeType") or "").strip(),
            mit_uuid=str(mi.get("mitypeURL") or "").rstrip("/").rsplit("/", 1)[-1],
            modification=modification,
            mi_number=str(mi.get("manufactureNum") or "").strip(),
            manufacture_year=year,
            organization=str(vri.get("organization") or "").strip(),
            owner=str(vri.get("miOwner") or "").strip(),
            verification_date=_iso(vri.get("vrfDate")),
            valid_date=_iso(vri.get("validDate")),
            method=str(vri.get("docTitle") or "").strip(),
            applicable=True if good is not None else False if bad is not None else None,
            docnum=str((good or {}).get("certNum") or (bad or {}).get("noticeNum") or "").strip(),
            additional_info=str(info.get("additional_info") or "").strip(),
            standards=standards,
            card_url=f"https://fgis.gost.ru/fundmetrology/cm/results/{vri_id}",
        )

    def as_dict(self) -> dict:
        return asdict(self)


class VriClient(FifClient):
    """Поиск прошлых поверок по заводскому номеру и карточка поверки."""

    def year_on(self, route: str, serial: str, year: int) -> list[dict]:
        """Записи одного года по одному пути (без кэша)."""
        if route == "mcp":
            payload = self._timed(route, lambda: rst_mcp.call(
                "search_verifications",
                {"mi_number": serial, "year": year, "rows": ROWS, "sort": "verification_date desc"},
                timeout=max(self.timeout, 20),
            ))
        else:
            payload = self._timed(route, lambda: self._get(
                "vri", {"mi_number": serial, "year": year, "rows": ROWS, "start": 0,
                        "sort": "verification_date desc"}))
        return list(self._rows(payload))

    def _year(self, serial: str, year: int) -> list[dict]:
        return self._on_routes(lambda route: self.year_on(route, serial, year))

    def _collect(self, serial: str, years: list[int]) -> tuple[list[VriRecord], list[int], str]:
        """Записи по годам. Первый год спрашиваем отдельно: если реестр лежит целиком, отказ
        приходит после одной попытки, а не после восьми таймаутов подряд."""
        rows = self._year(serial, years[0])  # FifUnavailable — наружу: реестр недоступен
        found = [VriRecord.parse(row) for row in rows]
        failed: list[int] = []
        error = ""
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {year: pool.submit(self._year, serial, year) for year in years[1:]}
        for year, future in futures.items():
            try:
                found.extend(VriRecord.parse(row) for row in future.result())
            except FifUnavailable as exc:
                failed.append(year)
                error = str(exc)
        return found, failed, error

    def history(self, serial: str, *, today: dt.date | None = None) -> VriHistory:
        """Поверки прибора за последние годы, свежие первыми.

        ``FifUnavailable`` — если реестр не ответил уже на текущий год. Сбой на отдельных прошлых
        годах отказом не считается: частичный ответ полезнее (``failed_years`` — каких лет не хватает).
        """
        serial = (serial or "").strip()
        today = today or dt.date.today()
        years = list(range(today.year, today.year - years_back() - 1, -1))
        span = (years[-1], years[0])
        if len(serial) < 3:
            return VriHistory(serial=serial, years=span)

        key = "fif:vri:v1:" + hashlib.md5(f"{serial_key(serial)}:{span}".encode()).hexdigest()
        cached = fif_status.cache_get(key)
        if cached is not None:
            return VriHistory(
                serial=cached["serial"], items=[VriRecord(**item) for item in cached["items"]],
                years=tuple(cached["years"]), failed_years=cached["failed_years"], error=cached["error"],
            )

        wanted = serial_key(serial)
        items, failed, error = self._collect(serial_pattern(serial), years)
        seen: set[str] = set()
        unique = [
            i for i in items
            if serial_key(i.mi_number) == wanted and not (i.vri_id in seen or seen.add(i.vri_id))
        ]
        result = VriHistory(
            serial=serial, years=span, failed_years=failed, error=error,
            items=sorted(unique, key=lambda i: i.verification_date or "", reverse=True),
        )
        if not result.partial:  # неполный ответ не запоминаем — пусть следующий запрос доберёт
            fif_status.cache_set(key, result.as_dict(), HISTORY_TTL)
        return result

    def card_of(self, route: str, vri_id: str) -> Any:
        if route == "mcp":
            return self._timed(route, lambda: rst_mcp.call(
                "get_verification", {"vri_id": vri_id, "include_raw": True}, timeout=max(self.timeout, 20)))
        return self._timed(route, lambda: self._get(f"vri/{vri_id}"))

    def card(self, vri_id: str) -> VriCard:
        vri_id = (vri_id or "").strip().rstrip("/").rsplit("/", 1)[-1]
        if not re.fullmatch(r"\d+-\d+", vri_id):
            raise FifUnavailable(f"Непонятный номер записи о поверке: {vri_id}")
        key = f"fif:vri:card:{vri_id}"
        cached = fif_status.cache_get(key)
        if cached is not None:
            return VriCard.parse(cached, vri_id)
        payload = self._on_routes(lambda route: self.card_of(route, vri_id))
        card = VriCard.parse(payload, vri_id)
        fif_status.cache_set(key, payload, CARD_TTL)
        return card
