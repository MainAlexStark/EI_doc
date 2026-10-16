"""Клиент открытого реестра ФИФ ОЕИ (ФГИС «Аршин», раздел «Утверждённые типы СИ»).

Зачем: при заведении СИ поверитель вводит номер в Госреестре или название, а
система показывает готовые варианты **с изготовителем** и по выбранному типу
подтягивает карточку целиком — модификации, межповерочный интервал, срок
действия свидетельства и файлы (методика поверки, описание типа).

Формат ответа сверен с живым API 16.10.2026 (через MCP-сервер РСТ):

* ``GET /mit?search=*СВК*&rows=20&start=0`` →
  ``{"result": {"count": N, "items": [{"mit_uuid", "number", "title",
  "notation", "manufacturers"}]}}``. ФГИС объединяет слова запроса через ИЛИ,
  поэтому в неё уходит одно, самое редкое слово в звёздочках, а остальные
  слова проверяются здесь (см. ``search_types``).
* ``GET /mit/<mit_uuid>`` → ``{"result": {"general": {...}, "mpi": [...],
  "meth": [...], "spec": [...], "manufacturer": [...]}}``.
* ``GET /doc/<uuid>`` — файл (PDF) методики поверки или описания типа.

ФГИС отвечает только российским адресам и держит лимит 2 запроса в секунду с
адреса — клиент сам выдерживает паузу между запросами. Разбор по-прежнему
терпим к обёрткам и именам полей: если формат снова поменяется, ``manage.py
fif_probe`` покажет фактический ответ.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import unquote

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://fgis.gost.ru/fundmetrology/eapi"
CACHE_TTL = 60 * 60 * 12
CARD_CACHE_TTL = 60 * 60 * 24
MIN_INTERVAL = 0.55  # с — лимит ФГИС 2 запроса в секунду с адреса

# Имя нашего поля -> вероятные имена в ответе ФИФ, по порядку предпочтения.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "registry_number": ("number", "reg_number", "regNumber", "num_mit", "registry_number"),
    "title": ("title", "mit_title", "name", "nameSi"),
    "notation": ("notation", "designation", "mit_notation", "type"),
    "manufacturer": ("manufacturers", "manufacturer", "manufactorer", "producer", "manufacturerName", "izg"),
    "interval_months": ("mpi", "interval", "verification_interval"),
    "uuid": ("mit_uuid", "id", "uuid", "rowId"),
}

_throttle_lock = threading.Lock()
_last_request = 0.0


def split_notation(notation: str) -> list[str]:
    """Модификации из обозначения типа.

    «СХВ (СХВ-15, СХВ-15Д), СГВ (СГВ-15)» → ["СХВ-15", "СХВ-15Д", "СГВ-15"];
    «типа СВК» → ["СВК"]; «СВК, СВКМ» → ["СВК", "СВКМ"].
    """
    text = (notation or "").replace("\n", " ").strip()
    if not text:
        return []
    parts: list[str] = []
    depth, current = 0, ""
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        if char in ",;" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)

    variants: list[str] = []
    for part in parts:
        part = part.strip()
        inner = re.search(r"[\(\[](.*)[\)\]]", part)
        if inner and inner.group(1).strip():
            pieces = [p.strip() for p in re.split(r"[,;]", inner.group(1))]
        else:
            pieces = [part]
        for piece in pieces:
            piece = re.sub(r"^(типа|тип|модели|модель|модификаци[ия])\s+", "", piece, flags=re.I)
            piece = piece.strip(" .«»\"'")
            if piece and piece not in variants:
                variants.append(piece)
    return variants


@dataclass
class FifType:
    """Кандидат типа СИ из Госреестра."""

    registry_number: str = ""
    title: str = ""
    notation: str = ""
    manufacturer: str = ""
    interval_months: int | None = None
    uuid: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def variants(self) -> list[str]:
        return split_notation(self.notation)

    @property
    def name(self) -> str:
        """Как тип назовём у себя: первая модификация, иначе наименование типа."""
        variants = self.variants
        return variants[0] if variants else self.title

    @property
    def label(self) -> str:
        """Строка для списка подсказок — изготовитель обязателен."""
        parts = [self.notation or self.title or "без наименования"]
        if self.registry_number:
            parts.append(f"№ {self.registry_number}")
        parts.append(self.manufacturer or "изготовитель не указан")
        return " · ".join(parts)


@dataclass
class FifDocumentRef:
    kind: str  # meth — методика поверки, spec — описание типа
    title: str
    url: str

    @property
    def doc_uuid(self) -> str:
        return self.url.rstrip("/").rsplit("/", 1)[-1]


@dataclass
class FifCard:
    """Карточка утверждённого типа — всё, что нужно, чтобы завести тип у себя."""

    uuid: str
    registry_number: str
    title: str
    notation: str
    manufacturer: str
    valid_to: dt.date | None
    mpi: list[dict]  # [{"parameter", "months", "valid_from"}], актуальные
    documents: list[FifDocumentRef]
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def variants(self) -> list[str]:
        return split_notation(self.notation)

    @property
    def is_valid(self) -> bool | None:
        return None if self.valid_to is None else self.valid_to >= dt.date.today()

    def as_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "registry_number": self.registry_number,
            "title": self.title,
            "notation": self.notation,
            "variants": self.variants,
            "manufacturer": self.manufacturer,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "is_valid": self.is_valid,
            "mpi": self.mpi,
            "documents": [
                {"kind": d.kind, "title": d.title, "url": d.url, "doc_uuid": d.doc_uuid} for d in self.documents
            ],
        }


class FifUnavailable(RuntimeError):
    """Реестр не ответил. Заведение СИ должно продолжаться вручную."""


def _date(value) -> dt.date | None:
    if not value:
        return None
    text = str(value)[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def actual_mpi(rows: list[dict]) -> list[dict]:
    """Межповерочные интервалы, действующие сейчас: с самой поздней даты начала."""
    parsed = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            months = int(str(row.get("mpi") or row.get("value") or "").strip())
        except ValueError:
            continue
        parsed.append({
            "parameter": str(row.get("parameter") or "").strip(),
            "months": months,
            "valid_from": row.get("valid_from") or "",
        })
    if not parsed:
        return []
    latest = max(str(item["valid_from"]) for item in parsed)
    return [item for item in parsed if str(item["valid_from"]) == latest]


class FifClient:
    def __init__(self, base_url: str | None = None, timeout: float = 8.0) -> None:
        self.base_url = (base_url or getattr(settings, "ARSHIN_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout

    # -- низкий уровень -------------------------------------------------
    @staticmethod
    def _wait_turn() -> None:
        global _last_request
        with _throttle_lock:
            pause = MIN_INTERVAL - (time.monotonic() - _last_request)
            if pause > 0:
                time.sleep(pause)
            _last_request = time.monotonic()

    def _request(self, path: str, params: dict | None = None, *, timeout: float | None = None) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        self._wait_turn()
        try:
            response = requests.get(url, params=params, timeout=timeout or self.timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise FifUnavailable(f"Реестр ФИФ недоступен: {exc}") from exc

    def _get(self, path: str, params: dict | None = None) -> Any:
        response = self._request(path, params)
        try:
            return response.json()
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

        notation = values.get("notation") or ""
        if isinstance(notation, list):
            notation = ", ".join(str(item) for item in notation)

        return FifType(
            registry_number=str(values.get("registry_number") or "").strip(),
            title=str(values.get("title") or "").strip(),
            notation=str(notation).strip(),
            manufacturer=str(values.get("manufacturer") or "").strip(),
            interval_months=interval,
            uuid=str(values.get("uuid") or "").strip(),
            raw=row,
        )

    # -- поиск ------------------------------------------------------------
    @staticmethod
    def _words(query: str) -> list[str]:
        return [w for w in re.split(r"[\s,;]+", query.strip()) if w]

    @staticmethod
    def _matches(item: FifType, words: list[str]) -> bool:
        haystack = " ".join([item.registry_number, item.title, item.notation, item.manufacturer]).lower()
        compact = haystack.replace(" ", "").replace("-", "")
        return all(w.lower() in haystack or w.lower().replace("-", "") in compact for w in words)

    def search_types(self, query: str, *, limit: int = 20) -> list[FifType]:
        """Поиск типов СИ по номеру в Госреестре, обозначению, наименованию или изготовителю.

        ФГИС объединяет слова через ИЛИ: «СВК 15» вернёт всё со словом «15».
        Поэтому в ФГИС уходит одно слово — самое длинное из тех, где есть
        буквы (обозначение обычно реже цифр), — а по остальным словам
        результаты сортируются: сначала совпавшие со всеми. Совсем не
        совпавшие не выбрасываем: модификации («СВК 15-3-2») в реестре часто
        нет, там только «типа СВК».
        """
        query = (query or "").strip()
        words = self._words(query)
        if len(query) < 2 or not words:
            return []

        key = "fif:types:v3:" + hashlib.md5(f"{query.lower()}:{limit}".encode()).hexdigest()
        cached = cache.get(key)
        if cached is not None:
            return [FifType(**item) for item in cached]

        with_letters = [w for w in words if re.search(r"[^\W\d_]", w)]
        anchor = max(with_letters or words, key=len)
        rest = [w for w in words if w is not anchor]
        fetch = 100 if rest else limit
        payload = self._get("mit", {"search": f"*{anchor}*", "rows": fetch, "start": 0})
        found = [self._parse(row) for row in self._rows(payload)]
        if rest:
            found.sort(key=lambda item: -sum(self._matches(item, [w]) for w in rest))
        found = found[:limit]

        cache.set(key, [item.__dict__ for item in found], CACHE_TTL)
        return found

    def raw_search(self, query: str, *, limit: int = 5) -> Any:
        """Сырой ответ — для команды fif_probe."""
        return self._get("mit", {"search": f"*{query}*", "rows": limit, "start": 0})

    # -- карточка типа ----------------------------------------------------
    def resolve_uuid(self, ident: str) -> str:
        """mit_uuid по самому uuid, ссылке на карточку или номеру в Госреестре."""
        ident = (ident or "").strip().rstrip("/")
        if re.fullmatch(r"[0-9a-fA-F-]{32,36}", ident):
            return ident
        if "/" in ident:
            return ident.rsplit("/", 1)[-1]
        for item in self.search_types(ident, limit=10):
            if item.registry_number == ident and item.uuid:
                return item.uuid
        raise FifUnavailable(f"Тип с номером {ident} в реестре не найден")

    @classmethod
    def parse_card(cls, payload: Any, uuid: str = "") -> FifCard:
        body = payload
        if isinstance(body, dict) and isinstance(body.get("result"), dict):
            body = body["result"]
        if isinstance(body, dict) and isinstance(body.get("card"), dict):  # формат MCP-сервера РСТ
            body = body["card"]
        if not isinstance(body, dict):
            raise FifUnavailable("Карточка типа пришла в неожиданном формате")

        general = body.get("general") if isinstance(body.get("general"), dict) else body
        notation = general.get("notation") or ""
        if isinstance(notation, list):
            notation = ", ".join(str(item) for item in notation)

        makers = body.get("manufacturer") or []
        if isinstance(makers, list):
            manufacturer = "; ".join(
                ", ".join(str(m.get(k)) for k in ("title", "country", "address") if m.get(k))
                for m in makers if isinstance(m, dict)
            )
        else:
            manufacturer = str(makers)

        documents: list[FifDocumentRef] = []
        for kind, default_title in (("meth", "Методика поверки"), ("spec", "Описание типа")):
            for doc in body.get(kind) or []:
                if isinstance(doc, dict) and doc.get("doc_url"):
                    title = str(doc.get("title") or default_title).strip()
                    if kind == "meth" and title == "Методики поверки":
                        title = default_title
                    documents.append(FifDocumentRef(kind=kind, title=title, url=str(doc["doc_url"])))

        return FifCard(
            uuid=str(general.get("mit_uuid") or uuid),
            registry_number=str(general.get("number") or "").strip(),
            title=str(general.get("title") or "").strip(),
            notation=str(notation).strip(),
            manufacturer=manufacturer.strip(),
            valid_to=_date(general.get("valid_to")),
            mpi=actual_mpi(body.get("mpi") or []),
            documents=documents,
            raw=body,
        )

    def get_type(self, ident: str) -> FifCard:
        """Карточка утверждённого типа по mit_uuid, ссылке или номеру в Госреестре."""
        uuid = self.resolve_uuid(ident)
        key = f"fif:card:{uuid}"
        cached = cache.get(key)
        if cached is not None:
            return self.parse_card(cached, uuid)
        payload = self._get(f"mit/{uuid}")
        card = self.parse_card(payload, uuid)
        cache.set(key, payload, CARD_CACHE_TTL)
        return card

    def download(self, url: str) -> tuple[bytes, str, str]:
        """Скачать документ типа: (содержимое, имя файла, MIME)."""
        response = self._request(url, timeout=60)
        content_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        disposition = response.headers.get("Content-Disposition", "")
        match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", disposition) or re.search(
            r'filename="?([^";]+)"?', disposition
        )
        filename = unquote(match.group(1)).strip() if match else ""
        if not filename:
            extension = ".pdf" if "pdf" in content_type else ""
            filename = url.rstrip("/").rsplit("/", 1)[-1] + extension
        return response.content, filename, content_type


def mark_ambiguous(candidates: list[FifType]) -> list[dict]:
    """Пометить кандидатов, у которых совпадает обозначение.

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
            "title": candidate.title,
            "manufacturer": candidate.manufacturer,
            "notation": candidate.notation,
            "interval_months": candidate.interval_months,
            "fif_uuid": candidate.uuid,
            "label": candidate.label,
            "ambiguous": counts.get(candidate.name.strip().lower(), 0) > 1,
        }
        for candidate in candidates
    ]
