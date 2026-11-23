"""Инструменты ассистента для режима «Закупки»: документы, поиск, выжимка, план заходов.

Права — как у экрана закупок (apps.logistics.api): читать закупку может тот, кому она
видна (руководство — все, поверитель — закупки своих командировок); выжимку и заходы
предлагает и подтверждает руководство/менеджер. Запись — только карточкой
``PendingAction`` с кнопкой «Подтвердить», исполнитель заново проверяет права нажавшего.
"""

from __future__ import annotations

import datetime as dt

from rest_framework.exceptions import PermissionDenied

from apps.assistant.actions import ActionError
from apps.assistant.models import PendingAction
from apps.assistant.tools import INT, STR, Tool, ToolContext, ToolFailure, ToolResult, _dump, _obj, _slice, _source
from apps.logistics import procurement_docs
from apps.logistics.api import is_manager, visible_procurements
from apps.logistics.models import Procurement, TextStatus

SECTION_HINT = (
    "summary: {requirements: [{text, source}] — требования к исполнителю (аккредитация, эталоны, опыт, СРО); "
    "instruments: [{name, type, registry_number, quantity, address, note, source}] — перечень СИ, адрес объекта "
    "для каждой строки; stages: [{title, date_from, date_to (YYYY-MM-DD), note, source}] — сроки и этапы; "
    "access: [{text, source}] — пропуска, допуск, инструктажи, документы на людей и машины; contacts: [{name, "
    "position, phone, email, note}] — контакты заказчика; penalties: [{text, source}] — штрафы и неустойки; "
    "deadline: YYYY-MM-DD; notes}. source — «документ, пункт/страница»."
)


def _procurement(ctx: ToolContext, args: dict) -> Procurement:
    pk = args.get("procurement_id")
    item = visible_procurements(ctx.user).filter(pk=pk).first() if pk else None
    if item is None:
        raise ToolFailure(f"Закупки с id {pk} нет или она вам не видна")
    return item


def _require_manager(ctx: ToolContext) -> None:
    if not is_manager(ctx.user):
        raise ToolFailure("Выжимку и заходы по закупке меняют руководство и менеджер")


def _label(item: Procurement) -> str:
    return f"Закупка {item.label} · {item.customer.name}"


# ===========================================================================
# Чтение
# ===========================================================================
def ei_procurement_documents(ctx: ToolContext, args: dict) -> ToolResult:
    item = _procurement(ctx, args)
    docs = list(item.documents.only("id", "title", "kind", "text_status", "text_error", "created_at"))
    listing = [
        {"id": d.pk, "title": d.title, "kind": d.get_kind_display(), "text": d.get_text_status_display(),
         "error": d.text_error}
        for d in docs
    ]
    if not args.get("document_id"):
        out = {"procurement": _label(item), "documents": listing,
               "summary_saved": bool(item.summary), "summary_at": item.summary_at}
        return ToolResult(_dump(out), {"sources": [_source("ei", _label(item))]})
    doc = item.documents.filter(pk=args["document_id"]).first()
    if doc is None:
        raise ToolFailure(f"У закупки нет документа {args['document_id']}. Документы: {_dump(listing)}")
    if doc.text_status != TextStatus.DONE:
        return ToolResult(f"«{doc.title}»: текста нет ({doc.get_text_status_display()}; {doc.text_error}). "
                          "Скан без текстового слоя попросите загрузить картинками в чат.")
    part, tail = _slice(doc.text, args.get("offset") or 0, args.get("max_chars") or 30000)
    head = f"{doc.get_kind_display()}: «{doc.title}» (документ [{doc.pk}] закупки {item.label})\n\n"
    return ToolResult(head + part + tail, {"sources": [_source("ei", f"{doc.title} ({item.label})",
                                                               ref=f"procurement-document:{doc.pk}")]})


def ei_procurement_search(ctx: ToolContext, args: dict) -> ToolResult:
    item = _procurement(ctx, args)
    query = str(args.get("query") or "").strip()
    if len(query) < 2:
        raise ToolFailure("query — хотя бы 2 символа")
    hits = procurement_docs.search(item, query)
    if not hits:
        unread = item.documents.exclude(text_status=TextStatus.DONE).count()
        tail = f" Без текста документов: {unread}." if unread else ""
        return ToolResult(f"В документах закупки {item.label} ничего не нашлось по «{query}».{tail} "
                          "Попробуйте другие слова (номер пункта, «пропуск», «неустойка», «перечень»).")
    sources = [_source("ei", f"{h['title']} ({item.label})", ref=f"procurement-document:{h['document_id']}") for h in hits]
    return ToolResult(_dump(hits), {"sources": sources, "summary": f"Документов с совпадениями: {len(hits)}"})


# ===========================================================================
# Выжимка и план заходов — карточкой «Подтвердить»
# ===========================================================================
def _summary_view(summary: dict) -> list:
    view = []
    for key, title in procurement_docs.SECTION_TITLES.items():
        rows = summary.get(key) or []
        if not rows:
            continue
        if key == "instruments":
            text = "; ".join(f"{r['name']} × {r['quantity']}" + (f" ({r['address']})" if r["address"] else "") for r in rows[:15])
            text += f" … всего строк {len(rows)}, приборов {sum(r['quantity'] for r in rows)}" if len(rows) > 15 else ""
        elif key == "stages":
            text = "; ".join(f"{r['title']}" + (f" до {r['date_to']}" if r["date_to"] else "") for r in rows[:10])
        elif key == "contacts":
            text = "; ".join(", ".join(filter(None, [r["name"], r["position"], r["phone"], r["email"]])) for r in rows[:10])
        else:
            text = "; ".join(r["text"] for r in rows[:8])
        view.append([title, text[:1500]])
    if summary.get("deadline"):
        view.append(["Срок исполнения", summary["deadline"]])
    return view


def ei_procurement_summary_propose(ctx: ToolContext, args: dict) -> ToolResult:
    item = _procurement(ctx, args)
    _require_manager(ctx)
    try:
        summary = procurement_docs.clean_summary(args.get("summary"))
    except procurement_docs.SummaryError as exc:
        raise ToolFailure(f"{exc}. Формат: {SECTION_HINT}") from exc
    sources = [str(s) for s in (args.get("sources") or []) if str(s).strip()]
    if not sources:
        raise ToolFailure("Укажи sources — из каких документов закупки (название, пункты) взята выжимка")
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="procurement_summary_save",
        title=f"Выжимка по закупке {item.label}"[:250],
        payload={"procurement_id": item.pk, "summary": summary, "view": _summary_view(summary)},
        sources=sources,
    )
    note = ("Карточка выжимки показана человеку с кнопкой «Подтвердить». После подтверждения она сохранится в "
            "закупке (кто и когда — отмечается), прежняя версия останется в истории.")
    if item.summary:
        note += " ВНИМАНИЕ: у закупки уже есть выжимка — новая её заменит, скажи об этом человеку."
    return ToolResult(note, {"action": action.pk})


def _date_arg(value, name: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ToolFailure(f"{name} — дата YYYY-MM-DD") from exc


def _int_arg(value, name: str, default: int, low: int, high: int) -> int:
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolFailure(f"{name} — целое число от {low} до {high}") from None
    if not low <= number <= high:
        raise ToolFailure(f"{name} — целое число от {low} до {high}, а не {number}")
    return number


def ei_procurement_visits_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.core.models import Employee

    item = _procurement(ctx, args)
    _require_manager(ctx)
    lead = Employee.objects.filter(pk=args.get("lead_id"), is_active=True).first() if args.get("lead_id") else None
    if args.get("lead_id") and lead is None:
        raise ToolFailure(f"Сотрудника {args['lead_id']} нет — посмотри ei_team или ei_staff")
    per_day = _int_arg(args.get("per_day"), "per_day", 40, 1, 1000)
    days_per_trip = _int_arg(args.get("days_per_trip"), "days_per_trip", 5, 1, 30)
    try:
        plan = procurement_docs.plan_visits(
            item, per_day=per_day, days_per_trip=days_per_trip,
            start=_date_arg(args.get("start"), "start"),
        )
    except procurement_docs.SummaryError as exc:
        raise ToolFailure(str(exc)) from exc
    view = [["Закупка", _label(item)], ["Приборов", str(plan["instruments_total"])],
            ["Адресов", str(plan["addresses"])], ["В день", str(plan["per_day"])]]
    for trip in plan["trips"]:
        view.append([f"Заход {trip['number']}", f"{trip['date_start']} – {trip['date_end']}: {trip['quantity']} приборов, "
                     + "; ".join(f"{d['date']}: {', '.join(s['address'] for s in d['stops'][:4])}" for d in trip["days"][:5])])
    for warning in plan["warnings"]:
        view.append(["Внимание", warning])
    payload = {"procurement_id": item.pk, "plan": plan, "lead_id": lead.pk if lead else None,
               "member_ids": [int(x) for x in args.get("member_ids") or [] if str(x).isdigit()], "view": view}
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="procurement_visits_create",
        title=f"Заходы по закупке {item.label}: {len(plan['trips'])}"[:250], payload=payload,
    )
    return ToolResult(
        "Карточка плана заходов показана человеку с кнопкой «Подтвердить»: после подтверждения заведутся "
        f"командировки по закупке ({len(plan['trips'])}) с адресами по дням. План: {_dump(plan)[:6000]}",
        {"action": action.pk},
    )


def _summary_save(action: PendingAction, user) -> dict:
    if not is_manager(user):
        raise PermissionDenied("Выжимку по закупке сохраняют руководство и менеджер")
    item = Procurement.objects.filter(pk=action.payload["procurement_id"]).first()
    if item is None:
        raise ActionError("Закупка удалена")
    try:
        procurement_docs.save_summary(item, user, action.payload["summary"], via_assistant=True, sources=action.sources)
    except procurement_docs.SummaryError as exc:
        raise ActionError(str(exc)) from exc
    return {"procurement_id": item.pk, "label": item.label,
            "instruments": len(item.summary.get("instruments") or [])}


def _visits_create(action: PendingAction, user) -> dict:
    from apps.core.models import Employee
    from apps.logistics import service

    if not is_manager(user):
        raise PermissionDenied("Заходы по закупке заводят руководство и менеджер")
    p = action.payload
    item = Procurement.objects.filter(pk=p["procurement_id"]).first()
    if item is None:
        raise ActionError("Закупка удалена")
    lead = (Employee.objects.filter(pk=p.get("lead_id"), is_active=True).first() if p.get("lead_id")
            else getattr(user, "employee", None))
    if lead is None:
        raise ActionError("Не выбран старший — у нажавшего нет карточки сотрудника")
    members = list(Employee.objects.filter(pk__in=p.get("member_ids") or [], is_active=True))
    try:
        trips = procurement_docs.create_visits(item, p["plan"], lead=lead, members=members)
    except (procurement_docs.SummaryError, service.TripError) as exc:
        raise ActionError(str(exc)) from exc
    return {"procurement_id": item.pk, "trips": [{"id": t.pk, "title": t.title} for t in trips]}


EXECUTORS = {"procurement_summary_save": _summary_save, "procurement_visits_create": _visits_create}

DONE_TEXT = {
    "procurement_summary_save": lambda r: (
        f"Выжимка по закупке {r['label']} сохранена (строк перечня СИ: {r['instruments']}). Видна в карточке закупки."
    ),
    "procurement_visits_create": lambda r: (
        "Заведены командировки по закупке: " + "; ".join(f"{t['title']} (id {t['id']})" for t in r["trips"]) + "."
    ),
}

TOOLS: list[Tool] = [
    Tool(
        "ei_procurement_documents", "Смотрю документы закупки",
        "Документы закупки (ТЗ, контракт, заявка на пропуска, письма): без document_id — список с состоянием "
        "текста и есть ли уже выжимка; с document_id — текст документа частями (offset/max_chars). "
        "procurement_id — id закупки EI_doc.",
        _obj({"procurement_id": INT, "document_id": INT, "offset": INT, "max_chars": INT}, ["procurement_id"]),
        ei_procurement_documents,
    ),
    Tool(
        "ei_procurement_search", "Ищу в документах закупки",
        "Поиск по тексту всех документов закупки: документы, где есть все слова запроса, с фрагментами и номерами "
        "страниц. Ищи короткими словами: «пропуск», «неустойка», «перечень», «аккредитац», номер пункта.",
        _obj({"procurement_id": INT, "query": STR}, ["procurement_id", "query"]),
        ei_procurement_search,
    ),
    Tool(
        "ei_procurement_summary_propose", "Готовлю выжимку по закупке",
        "Предложить выжимку из документов закупки — карточкой «Подтвердить» (руководство/менеджер). Сначала прочитай "
        "документы (ei_procurement_documents/ei_procurement_search), выжимку бери только из них. " + SECTION_HINT
        + " sources — документы и пункты, откуда взято.",
        _obj({"procurement_id": INT, "summary": {"type": "object"},
              "sources": {"type": "array", "items": STR}}, ["procurement_id", "summary", "sources"]),
        ei_procurement_summary_propose,
    ),
    Tool(
        "ei_procurement_visits_propose", "Планирую заходы по закупке",
        "Предложить план заходов/командировок по перечню СИ из выжимки (если её нет — по объёму контракта): адреса "
        "по рабочим дням (per_day приборов в день, по умолчанию 40), дни — в заходы по days_per_trip (5). start — "
        "первый день (по умолчанию начало работ по контракту или завтра). lead_id/member_ids — кто едет. Карточка "
        "«Подтвердить» заведёт командировки по закупке с адресами.",
        _obj({"procurement_id": INT, "per_day": INT, "days_per_trip": INT, "start": STR, "lead_id": INT,
              "member_ids": {"type": "array", "items": INT}}, ["procurement_id"]),
        ei_procurement_visits_propose,
    ),
]
