"""Документы закупки для агента и человека: текст, поиск, выжимка, план заходов.

Поток (режим «Закупки»):

1. Менеджер загружает ТЗ, контракт, заявку на пропуска — текст извлекается в фоне
   (``schedule_extract`` → Celery ``extract_procurement_document``; без брокера — сразу).
2. Человек и ассистент ищут по тексту всех документов закупки (``search``).
3. Ассистент заполняет **выжимку** (требования к исполнителю, перечень СИ с адресами,
   сроки и этапы, пропуска и допуск, контакты заказчика, штрафы) — карточкой
   «Подтвердить»; человек может поправить её сам. Хранится на закупке с отметкой
   кто и когда (``save_summary``), прежние версии — в истории закупки.
4. По перечню СИ из выжимки предлагается план заходов: адреса по дням и командировкам
   (``plan_visits``), подтверждённый план заводит командировки (``create_visits``).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import threading

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.logistics.models import Procurement, ProcurementDocument, TextStatus, Trip, TripKind

logger = logging.getLogger(__name__)

MAX_SNIPPETS = 5
SNIPPET_RADIUS = 160
PAGE_RE = re.compile(r"\[стр\. (\d+)\]")


class SummaryError(ValueError):
    """Выжимка или план не прошли проверку — текст показывается как есть."""


# ---------------------------------------------------------------------------
# Текст документов
# ---------------------------------------------------------------------------
def extract(doc: ProcurementDocument) -> ProcurementDocument:
    """Извлечь и сохранить текст документа. Никогда не бросает: причина — в ``text_error``."""
    from apps.assistant.extract import ExtractError, extract_text

    try:
        with doc.file.open("rb") as fh:
            data = fh.read()
        text = extract_text(data, doc.file.name)
        doc.text, doc.text_status, doc.text_error = text, TextStatus.DONE if text.strip() else TextStatus.EMPTY, ""
    except ExtractError as exc:
        empty = "текстового слоя" in str(exc)
        doc.text, doc.text_status, doc.text_error = "", TextStatus.EMPTY if empty else TextStatus.FAILED, str(exc)[:300]
    except (OSError, ValueError) as exc:
        doc.text, doc.text_status, doc.text_error = "", TextStatus.FAILED, f"Файл не открылся: {exc}"[:300]
    doc.text_extracted_at = timezone.now()
    doc.save(update_fields=["text", "text_status", "text_error", "text_extracted_at"])
    return doc


def extract_by_id(doc_id: int) -> None:
    doc = ProcurementDocument.objects.filter(pk=doc_id).first()
    if doc is not None and doc.file:
        extract(doc)


def schedule_extract(doc: ProcurementDocument) -> None:
    """Текст — в фоне после коммита. Режим как у ассистента (ASSISTANT_RUN_MODE): sync / thread / celery;
    недоступный брокер не теряет документ — читаем сразу в этом процессе."""
    doc_id = doc.pk
    mode = getattr(settings, "ASSISTANT_RUN_MODE", "celery")
    if mode == "sync":
        transaction.on_commit(lambda: extract_by_id(doc_id))
        return
    if mode == "thread":
        transaction.on_commit(lambda: threading.Thread(target=extract_by_id, args=(doc_id,), daemon=True).start())
        return

    def send() -> None:
        try:
            from apps.logistics.tasks import extract_procurement_document

            extract_procurement_document.delay(doc_id)
        except Exception as exc:  # noqa: BLE001 — нет брокера: читаем сразу
            logger.warning("Очередь недоступна, читаю документ %s сразу: %s", doc_id, exc)
            extract_by_id(doc_id)

    transaction.on_commit(send)


def page_at(text: str, offset: int) -> int | None:
    """Номер страницы PDF по смещению в тексте (метки «[стр. N]» ставит извлечение)."""
    page = None
    for match in PAGE_RE.finditer(text, 0, offset + 1):
        page = int(match.group(1))
    return page


def _words(query: str) -> list[str]:
    return [w for w in re.split(r"\s+", (query or "").strip()) if len(w) >= 2][:8]


def search(procurement: Procurement, query: str, *, limit: int = 20) -> list[dict]:
    """Поиск по тексту документов закупки: все слова запроса в документе, фрагменты вокруг совпадений."""
    words = _words(query)
    if not words:
        return []
    qs = procurement.documents.filter(text_status=TextStatus.DONE)
    for word in words:
        qs = qs.filter(text__icontains=word)
    phrase = re.compile(re.escape(" ".join(words)), re.IGNORECASE)
    any_word = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    results = []
    for doc in qs.only("id", "title", "kind", "text")[:limit]:
        text = doc.text
        matches = list(phrase.finditer(text)) or list(any_word.finditer(text))
        snippets, last_end = [], -1
        for match in matches:
            if match.start() < last_end:
                continue
            start, end = max(0, match.start() - SNIPPET_RADIUS), min(len(text), match.end() + SNIPPET_RADIUS)
            snippets.append({
                "offset": match.start(), "page": page_at(text, match.start()),
                "text": ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else ""),
            })
            last_end = end
            if len(snippets) >= MAX_SNIPPETS:
                break
        results.append({
            "document_id": doc.pk, "title": doc.title, "kind": doc.kind, "kind_display": doc.get_kind_display(),
            "hits": len(matches), "snippets": snippets,
        })
    results.sort(key=lambda row: -row["hits"])
    return results


# ---------------------------------------------------------------------------
# Выжимка
# ---------------------------------------------------------------------------
TEXT_SECTIONS = ("requirements", "access", "penalties")
SECTION_TITLES = {
    "requirements": "Требования к исполнителю",
    "instruments": "Перечень СИ",
    "stages": "Сроки и этапы",
    "access": "Пропуска и допуск",
    "contacts": "Контакты заказчика",
    "penalties": "Штрафы и неустойки",
}


def _s(value, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _date(value) -> str:
    text = _s(value, 10)
    if not text:
        return ""
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise SummaryError(f"дата «{value}» — нужен формат ГГГГ-ММ-ДД") from exc


def _list(raw, name: str) -> list:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise SummaryError(f"{name} — список")
    if len(raw) > 2000:
        raise SummaryError(f"{name}: больше 2000 строк")
    return raw


def clean_summary(raw) -> dict:
    """Привести выжимку к одному виду. Строка в текстовом разделе — пункт ``{"text"}``;
    ``source`` у пункта — откуда взято (документ, пункт, страница)."""
    if not isinstance(raw, dict):
        raise SummaryError("выжимка — объект с разделами " + ", ".join(SECTION_TITLES))
    out: dict = {}
    for section in TEXT_SECTIONS:
        rows = []
        for item in _list(raw.get(section), section):
            item = {"text": item} if isinstance(item, str) else item
            if not isinstance(item, dict) or not _s(item.get("text")):
                raise SummaryError(f"{section}: пункт — строка или {{text, source}}")
            rows.append({"text": _s(item["text"], 2000), "source": _s(item.get("source"), 300)})
        out[section] = rows
    instruments = []
    for item in _list(raw.get("instruments"), "instruments"):
        if not isinstance(item, dict) or not _s(item.get("name")):
            raise SummaryError("instruments: у строки нужно name (наименование СИ)")
        try:
            quantity = int(item.get("quantity") or 1)
        except (TypeError, ValueError) as exc:
            raise SummaryError(f"instruments: quantity «{item.get('quantity')}» — целое число") from exc
        if not 0 < quantity <= 100000:
            raise SummaryError("instruments: quantity от 1 до 100000")
        instruments.append({
            "name": _s(item["name"], 250), "type": _s(item.get("type"), 250), "registry_number": _s(item.get("registry_number"), 40),
            "quantity": quantity, "address": _s(item.get("address"), 350), "note": _s(item.get("note"), 500),
            "source": _s(item.get("source"), 300),
        })
    out["instruments"] = instruments
    stages = []
    for item in _list(raw.get("stages"), "stages"):
        item = {"title": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not _s(item.get("title")):
            raise SummaryError("stages: у этапа нужно title")
        stages.append({
            "title": _s(item["title"], 300), "date_from": _date(item.get("date_from")), "date_to": _date(item.get("date_to")),
            "note": _s(item.get("note"), 1000), "source": _s(item.get("source"), 300),
        })
    out["stages"] = stages
    contacts = []
    for item in _list(raw.get("contacts"), "contacts"):
        item = {"name": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not (_s(item.get("name")) or _s(item.get("phone")) or _s(item.get("email"))):
            raise SummaryError("contacts: нужно name, phone или email")
        contacts.append({key: _s(item.get(key), 250) for key in ("name", "position", "phone", "email", "note", "source")})
    out["contacts"] = contacts
    out["deadline"] = _date(raw.get("deadline"))
    out["notes"] = _s(raw.get("notes"), 5000)
    return out


def save_summary(procurement: Procurement, user, raw, *, via_assistant: bool = False,
                 sources: list | None = None) -> Procurement:
    procurement.summary = clean_summary(raw)
    procurement.summary_by = user
    procurement.summary_at = timezone.now()
    procurement.summary_via_assistant = via_assistant
    procurement.summary_sources = [str(s)[:300] for s in (sources or []) if str(s).strip()][:50]
    procurement.save(update_fields=["summary", "summary_by", "summary_at", "summary_via_assistant", "summary_sources"])
    return procurement


def summary_data(procurement: Procurement) -> dict:
    user = procurement.summary_by
    employee = getattr(user, "employee", None) if user else None
    return {
        "procurement_id": procurement.pk,
        "summary": procurement.summary or {},
        "saved_by": (employee.full_name if employee else user.email) if user else "",
        "saved_at": procurement.summary_at,
        "via_assistant": procurement.summary_via_assistant,
        "sources": procurement.summary_sources,
        "sections": SECTION_TITLES,
    }


# ---------------------------------------------------------------------------
# План заходов по перечню СИ
# ---------------------------------------------------------------------------
NO_ADDRESS = "адрес не указан"


def _workdays(start: dt.date):
    day = start
    while True:
        if day.weekday() < 5:
            yield day
        day += dt.timedelta(days=1)


def plan_visits(procurement: Procurement, *, per_day: int = 40, days_per_trip: int = 5,
                start: dt.date | None = None) -> dict:
    """Предложение: адреса из перечня СИ → рабочие дни (не больше ``per_day`` приборов в день) →
    заходы (командировки по ``days_per_trip`` рабочих дней). Ничего не сохраняет."""
    if not 1 <= per_day <= 1000 or not 1 <= days_per_trip <= 30:
        raise SummaryError("per_day 1–1000, days_per_trip 1–30")
    rows = (procurement.summary or {}).get("instruments") or []
    source = "summary"
    if not rows:
        rows = [{"name": item.title, "quantity": item.quantity, "address": ""} for item in procurement.items.all()]
        source = "items"
    if not rows:
        raise SummaryError("Нет перечня СИ: заполните выжимку (instruments) или объём по контракту")

    groups: dict[str, dict] = {}
    for row in rows:
        address = _s(row.get("address"), 350) or NO_ADDRESS
        group = groups.setdefault(address.casefold(), {"address": address, "quantity": 0, "instruments": []})
        group["quantity"] += int(row.get("quantity") or 1)
        group["instruments"].append(f"{row.get('name')} × {int(row.get('quantity') or 1)}")

    begin = start or procurement.date_start or timezone.localdate() + dt.timedelta(days=1)
    days_iter = _workdays(begin)
    days: list[dict] = []
    current = {"date": next(days_iter), "stops": [], "quantity": 0}
    for group in sorted(groups.values(), key=lambda g: (g["address"] == NO_ADDRESS, g["address"])):
        left = group["quantity"]
        while left > 0:
            room = per_day - current["quantity"]
            if room <= 0:
                days.append(current)
                current = {"date": next(days_iter), "stops": [], "quantity": 0}
                room = per_day
            take = min(room, left)
            current["stops"].append({"address": group["address"], "quantity": take,
                                     "instruments": group["instruments"][:20]})
            current["quantity"] += take
            left -= take
    days.append(current)

    trips = []
    for index in range(0, len(days), days_per_trip):
        chunk = days[index: index + days_per_trip]
        trips.append({
            "number": len(trips) + 1,
            "date_start": chunk[0]["date"].isoformat(), "date_end": chunk[-1]["date"].isoformat(),
            "quantity": sum(day["quantity"] for day in chunk),
            "days": [{"date": day["date"].isoformat(), "quantity": day["quantity"], "stops": day["stops"]} for day in chunk],
        })
    warnings = []
    if NO_ADDRESS in {g["address"] for g in groups.values()}:
        warnings.append("У части СИ нет адреса объекта — уточните в выжимке перед выездом")
    if procurement.deadline and days and days[-1]["date"] > procurement.deadline:
        warnings.append(f"План выходит за срок контракта ({procurement.deadline:%d.%m.%Y})")
    return {
        "procurement_id": procurement.pk, "source": source, "per_day": per_day, "days_per_trip": days_per_trip,
        "instruments_total": sum(g["quantity"] for g in groups.values()), "addresses": len(groups),
        "trips": trips, "warnings": warnings,
    }


@transaction.atomic
def create_visits(procurement: Procurement, plan: dict, *, lead, members=()) -> list[Trip]:
    """Завести командировки по закупке из плана ``plan_visits``. Адрес в заходе — один раз
    (день — первый, приборов — сумма по дням)."""
    from apps.logistics import service

    created = []
    for trip_plan in plan.get("trips") or []:
        number = Trip.objects.filter(procurement=procurement).count() + 1
        trip = Trip.objects.create(
            kind=TripKind.PROCUREMENT, procurement=procurement, lead=lead,
            title=f"{procurement.customer.name} — заход {number}",
            date_start=dt.date.fromisoformat(trip_plan["date_start"]),
            date_end=dt.date.fromisoformat(trip_plan["date_end"]),
            note=f"По плану из перечня СИ: {trip_plan.get('quantity', 0)} приборов",
        )
        if members:
            trip.members.set([m for m in members if m.pk != lead.pk])
        stops: dict[str, dict] = {}
        for day in trip_plan.get("days") or []:
            for stop in day.get("stops") or []:
                if stop["address"] == NO_ADDRESS:
                    continue
                entry = stops.setdefault(stop["address"].casefold(), {
                    "address": stop["address"], "date": dt.date.fromisoformat(day["date"]), "quantity": 0,
                    "note": "; ".join(stop.get("instruments") or [])[:300],
                })
                entry["quantity"] += int(stop.get("quantity") or 0)
        for entry in stops.values():
            service.add_stop(trip, service.StopInput(
                address=entry["address"], planned_date=entry["date"], planned_count=entry["quantity"] or None,
                note=entry["note"], locate=False,
            ))
        created.append(trip)
    if not created:
        raise SummaryError("В плане нет ни одного захода")
    return created
