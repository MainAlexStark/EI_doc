"""Инструменты агента.

Каждый инструмент — JSON-схема для модели + обработчик. Обработчик
возвращает ``ToolResult``: ``content`` уходит модели, ``ui`` — человеку
(виджеты, источники, карточка действия, файлы).

Правило для всего, что меняет данные компании: инструмент НЕ пишет сам, а
заводит ``PendingAction`` — карточку с кнопкой «Подтвердить» в чате
(см. apps.assistant.actions).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from django.core.files.base import ContentFile
from django.db.models import Q

from apps.assistant import documents, knowledge, mcp_client, web
from apps.assistant.models import (
    Attachment,
    ContextMode,
    KnowledgeCategory,
    McpServer,
    MemoryItem,
    PendingAction,
    Scope,
)
from apps.equipment.calc import plan_help
from apps.verification.templates import template_help as verification_template_help

logger = logging.getLogger(__name__)

MAX_RESULT = 30_000


@dataclass
class ToolContext:
    user: object
    conversation: object
    run: object = None


@dataclass
class ToolResult:
    content: str
    ui: dict = field(default_factory=dict)
    is_error: bool = False


class ToolFailure(Exception):
    """Ошибка, которую стоит показать модели как есть — она поправит аргументы."""


@dataclass
class Tool:
    name: str
    label: str  # что видит человек: «Ищу в реестре ФГИС»
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], ToolResult]
    group: str = "builtin"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _source(kind: str, title: str, url: str = "", ref: str = "") -> dict:
    return {"kind": kind, "title": title[:300], "url": url, "ref": ref}


def _slice(text: str, offset: int, max_chars: int) -> tuple[str, str]:
    offset = max(0, int(offset or 0))
    max_chars = min(max(int(max_chars or 20000), 1000), 40000)
    part = text[offset: offset + max_chars]
    if offset + max_chars < len(text):
        tail = f"\n\n[показано {offset}–{offset + len(part)} из {len(text)} символов; дальше — offset={offset + len(part)}]"
    else:
        tail = f"\n\n[конец документа, всего {len(text)} символов]"
    return part, tail


# ===========================================================================
# Контекст компании
# ===========================================================================
def context_search(ctx: ToolContext, args: dict) -> ToolResult:
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolFailure("Пустой запрос")
    hits = knowledge.search(ctx.user, query, category=args.get("category") or "", limit=int(args.get("limit") or 8))
    if not hits:
        return ToolResult("Во внутренних файлах ничего не найдено по этому запросу. "
                          "Попробуйте другие слова (номер документа, обозначение типа) или веб-поиск.")
    rows, sources, seen = [], [], set()
    for chunk, score in hits:
        rows.append({
            "file_id": chunk.file_id, "file": chunk.file.title, "category": chunk.file.get_category_display(),
            "chunk": chunk.index, "section": chunk.heading, "text": chunk.text,
        })
        if chunk.file_id not in seen:
            seen.add(chunk.file_id)
            sources.append(_source("context", chunk.file.title, ref=f"file:{chunk.file_id}"))
    return ToolResult(_dump(rows), {"sources": sources, "summary": f"Найдено фрагментов: {len(rows)}"})


def context_read(ctx: ToolContext, args: dict) -> ToolResult:
    try:
        kf = knowledge.visible_files(ctx.user).get(pk=int(args.get("file_id")))
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure("Файл не найден — возьмите file_id из context_search или из списка файлов в промте") from exc
    if not kf.text:
        raise ToolFailure(f"У файла нет текста: {kf.extract_error or 'пусто'}")
    part, tail = _slice(kf.text, args.get("offset", 0), args.get("max_chars", 20000))
    return ToolResult(f"# {kf.title}\n\n{part}{tail}",
                      {"sources": [_source("context", kf.title, ref=f"file:{kf.pk}")]})


# ===========================================================================
# Память
# ===========================================================================
def memory_save(ctx: ToolContext, args: dict) -> ToolResult:
    text = (args.get("text") or "").strip()
    if len(text) < 3:
        raise ToolFailure("Пустая запись")
    scope = args.get("scope") or "personal"
    if scope == "company":
        action = PendingAction.objects.create(
            conversation=ctx.conversation, kind="memory_company_save",
            title="Запомнить для всей компании", payload={"text": text},
        )
        return ToolResult(
            "Общая память меняется только после подтверждения человеком — карточка показана. "
            "Не считай это сохранённым, пока не придёт событие о подтверждении.",
            {"action": action.pk},
        )
    item = MemoryItem.objects.create(
        scope=Scope.PERSONAL, user=ctx.user, text=text, created_by=ctx.user,
        source_conversation=ctx.conversation,
    )
    return ToolResult(f"Сохранено в личную память (id {item.pk}).",
                      {"summary": f"Запомнил: {text[:120]}"})


def memory_delete(ctx: ToolContext, args: dict) -> ToolResult:
    try:
        item = MemoryItem.objects.get(pk=int(args.get("memory_id")))
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure("Запись памяти не найдена") from exc
    if item.scope == Scope.PERSONAL:
        if item.user_id != ctx.user.pk:
            raise ToolFailure("Это чужая личная запись")
        item.delete()
        return ToolResult("Удалено из личной памяти.", {"summary": "Забыл запись из личной памяти"})
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="memory_company_delete",
        title="Удалить из общей памяти", payload={"memory_id": item.pk, "text": item.text},
    )
    return ToolResult("Удаление из общей памяти ждёт подтверждения человеком.", {"action": action.pk})


# ===========================================================================
# Интернет
# ===========================================================================
def web_search(ctx: ToolContext, args: dict) -> ToolResult:
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolFailure("Пустой запрос")
    try:
        results = web.search(query, page=int(args.get("page") or 0))
    except web.WebError as exc:
        raise ToolFailure(str(exc)) from exc
    if not results:
        return ToolResult("Ничего не найдено. Переформулируйте запрос.")
    sources = [_source("web", r["title"] or r["url"], url=r["url"]) for r in results[:5]]
    return ToolResult(_dump(results), {"sources": sources, "summary": f"Результатов: {len(results)}"})


def web_fetch(ctx: ToolContext, args: dict) -> ToolResult:
    url = (args.get("url") or "").strip()
    try:
        text, final_url = web.fetch(url)
    except web.WebError as exc:
        raise ToolFailure(str(exc)) from exc
    part, tail = _slice(text, args.get("offset", 0), args.get("max_chars", 20000))
    return ToolResult(f"URL: {final_url}\n\n{part}{tail}",
                      {"sources": [_source("web", final_url, url=final_url)]})


# ===========================================================================
# Файлы
# ===========================================================================
def attachment_read(ctx: ToolContext, args: dict) -> ToolResult:
    in_chat = Attachment.objects.filter(conversation=ctx.conversation, user=ctx.user)
    try:
        attachment = in_chat.get(pk=int(args.get("attachment_id")))
    except Exception as exc:  # noqa: BLE001
        files = [f"{a.pk} — {a.name}" for a in in_chat.order_by("pk")[:20]]
        raise ToolFailure(
            ("Файл не найден. Файлы этого чата: " + "; ".join(files)) if files else
            "В этом чате нет файлов. Не перебирай id: работай без образца (шаблон по "
            "методике и ГОСТ) и в ответе одной строкой скажи, что файл не дошёл."
        ) from exc
    if attachment.is_image:
        return ToolResult("Это изображение — оно уже приложено к сообщению человека, смотри его там.")
    if not attachment.text:
        raise ToolFailure(f"Текст не извлечён: {attachment.extract_error or 'пусто'}")
    part, tail = _slice(attachment.text, args.get("offset", 0), args.get("max_chars", 20000))
    return ToolResult(f"# {attachment.name}\n\n{part}{tail}",
                      {"sources": [_source("file", attachment.name, ref=f"attachment:{attachment.pk}")]})


def _save_file(ctx: ToolContext, name: str, data: bytes, mime: str) -> Attachment:
    attachment = Attachment(
        user=ctx.user, conversation=ctx.conversation, origin=Attachment.Origin.AGENT,
        name=name, mime=mime, size=len(data),
    )
    attachment.file.save(name, ContentFile(data), save=False)
    attachment.save()
    return attachment


MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "md": "text/markdown",
    "csv": "text/csv",
    "txt": "text/plain",
}


def _render(fmt: str, args: dict) -> bytes:
    try:
        if fmt == "docx":
            return documents.render_docx(args)
        if fmt == "pdf":
            return documents.render_pdf(args)
        if fmt == "xlsx":
            return documents.render_xlsx(args.get("sheets"))
        if fmt == "csv":
            return documents.render_csv(args.get("sheets"))
        if fmt in ("md", "txt"):
            content = args.get("content") or ""
            if not content:
                raise documents.DocumentError("Для md/txt нужен content")
            return content.encode("utf-8")
    except documents.DocumentError as exc:
        raise ToolFailure(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — Typst/python-docx: показать модели причину
        raise ToolFailure(f"Не удалось собрать {fmt}: {exc}") from exc
    raise ToolFailure(f"Неизвестный формат {fmt}")


def file_create(ctx: ToolContext, args: dict) -> ToolResult:
    formats = args.get("formats") or [args.get("format") or "docx"]
    files = []
    for fmt in formats:
        fmt = str(fmt).lower().lstrip(".")
        if fmt not in MIME:
            raise ToolFailure(f"Формат {fmt} не поддерживается: {', '.join(MIME)}")
        data = _render(fmt, args)
        attachment = _save_file(ctx, documents.safe_filename(args.get("filename") or args.get("title") or "документ", fmt), data, MIME[fmt])
        files.append({"id": attachment.pk, "name": attachment.name, "size": attachment.size, "mime": attachment.mime})
    return ToolResult(
        "Файлы созданы и показаны человеку карточками: " + ", ".join(f["name"] for f in files),
        {"files": files},
    )


FACT_STATUSES = ("confirmed", "derived", "missing")


def protocol_draft(ctx: ToolContext, args: dict) -> ToolResult:
    facts = args.get("facts") or []
    problems = []
    for i, fact in enumerate(facts):
        if not isinstance(fact, dict):
            problems.append(f"facts[{i}] — не объект")
            continue
        status = fact.get("status")
        if status not in FACT_STATUSES:
            problems.append(f"facts[{i}].status должен быть одним из {FACT_STATUSES}")
        if status != "missing" and not (fact.get("source") or "").strip():
            problems.append(f"facts[{i}] «{fact.get('field')}»: не указан источник значения")
    if not facts:
        problems.append("facts пуст — для протокола обязательна таблица происхождения данных")
    if problems:
        raise ToolFailure("; ".join(problems))

    spec = dict(args, draft=True)
    spec.setdefault("header", "org")
    spec.setdefault("footer", "Черновик подготовлен ассистентом EI_doc — проверить перед подписанием")
    name = args.get("filename") or args.get("title") or "протокол"
    files = []
    for fmt in ("docx", "pdf"):
        data = _render(fmt, spec)
        attachment = _save_file(ctx, documents.safe_filename(name, fmt), data, MIME[fmt])
        files.append({"id": attachment.pk, "name": attachment.name, "size": attachment.size, "mime": attachment.mime})

    missing = [f for f in facts if f.get("status") == "missing"]
    widget = {
        "type": "facts",
        "title": "Откуда взяты данные протокола",
        "rows": [
            {k: str(f.get(k, "")) for k in ("field", "value", "source", "url", "status")} for f in facts
        ],
    }
    note = (
        f"Черновик собран (Word + PDF с пометкой ЧЕРНОВИК). Не заполнено полей: {len(missing)} — "
        "перечисли их человеку и спроси значения." if missing else
        "Черновик собран (Word + PDF с пометкой ЧЕРНОВИК). Попроси человека проверить таблицу источников."
    )
    sources = [
        _source("doc", f.get("source", ""), url=f.get("url", "")) for f in facts
        if f.get("url") and f.get("status") != "missing"
    ][:10]
    return ToolResult(note, {"files": files, "widgets": [widget], "sources": sources})


# ===========================================================================
# Данные EI_doc
# ===========================================================================
def ei_si_types(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.catalog.models import SiType

    query = (args.get("query") or "").strip()
    qs = SiType.objects.filter(is_active=True).select_related("family")
    if query:
        cond = Q()
        for word in query.split():
            cond &= Q(registry_number__icontains=word) | Q(name__icontains=word) | Q(manufacturer__icontains=word)
        qs = qs.filter(cond)
    rows = [
        {
            "id": t.pk, "registry_number": t.registry_number, "name": t.name, "manufacturer": t.manufacturer,
            "family": t.family.name, "interval_months": t.verification_interval_months,
            "limits": t.limits, "needs_review": t.needs_review, "fif_url": t.fif_url,
        }
        for t in qs.order_by("name")[: int(args.get("limit") or 30)]
    ]
    if not rows:
        return ToolResult("В справочнике EI_doc таких типов нет. Ищи в ФГИС (mcp rst) и предложи завести тип.")
    return ToolResult(_dump(rows), {"sources": [_source("ei", "Справочник типов СИ EI_doc")]})


def ei_si_type_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.catalog.api_types import SiTypeWriteSerializer, can_create
    from apps.catalog.models import SiType

    if not can_create(ctx.user):
        raise ToolFailure("У этого пользователя нет права заводить типы СИ (наблюдатель)")
    payload = {k: args[k] for k in (
        "family", "registry_number", "name", "manufacturer", "verification_interval_months",
        "limits", "fif_url",
    ) if args.get(k) not in (None, "")}
    serializer = SiTypeWriteSerializer(data=payload)
    if not serializer.is_valid():
        raise ToolFailure("Проверка не пройдена: " + _dump(serializer.errors))
    data = serializer.validated_data
    existing = SiType.objects.filter(registry_number=data["registry_number"], name=data["name"]).first()
    if existing:
        return ToolResult(f"Такой тип уже есть в справочнике (id {existing.pk}) — заводить не нужно.")
    sources = args.get("sources") or []
    if not sources:
        raise ToolFailure("Укажи sources — откуда взяты номер, наименование, изготовитель, МПИ и характеристики")
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="si_type_create",
        title=f"Завести тип СИ: {data['name']} № {data['registry_number']}",
        payload={**payload, "family": data["family"].pk, "limits": data.get("limits") or {}},
        sources=sources,
    )
    return ToolResult(
        "Карточка нового типа показана человеку с кнопкой «Подтвердить». Тип будет заведён только после "
        "подтверждения — дождись события.",
        {"action": action.pk},
    )


def ei_journal(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.verification.models import Verification

    qs = Verification.objects.select_related(
        "instrument__si_type", "instrument__owner", "instrument__site", "verifier", "protocol__scope__series",
        "protocol__scope__employee", "method",
    ).order_by("-verified_at")
    if args.get("serial_number"):
        qs = qs.filter(instrument__serial_number__icontains=args["serial_number"].strip())
    if args.get("registry_number"):
        qs = qs.filter(instrument__si_type__registry_number__icontains=args["registry_number"].strip())
    if args.get("query"):
        for word in str(args["query"]).split():
            qs = qs.filter(
                Q(instrument__si_type__name__icontains=word) | Q(instrument__si_type__manufacturer__icontains=word)
                | Q(instrument__owner__name__icontains=word) | Q(instrument__site__address__icontains=word)
                | Q(instrument__serial_number__icontains=word)
            )
    for key, lookup in (("date_from", "verified_at__date__gte"), ("date_to", "verified_at__date__lte")):
        if args.get(key):
            try:
                qs = qs.filter(**{lookup: dt.date.fromisoformat(args[key])})
            except ValueError as exc:
                raise ToolFailure(f"{key}: дата в формате YYYY-MM-DD") from exc
    rows = []
    for v in qs[: min(int(args.get("limit") or 30), 100)]:
        protocol = getattr(v, "protocol", None)
        rows.append({
            "id": v.pk, "verified_at": v.verified_at.isoformat(timespec="minutes"),
            "si": v.instrument.si_type.name, "registry_number": v.instrument.si_type.registry_number,
            "manufacturer": v.instrument.si_type.manufacturer, "serial_number": v.instrument.serial_number,
            "year": v.instrument.manufacture_year,
            "owner": v.instrument.owner.name if v.instrument.owner else "",
            "address": v.instrument.site.address if v.instrument.site else "",
            "verifier": v.verifier.full_name, "suitable": v.suitable, "status": v.status,
            "protocol": protocol.full_number if protocol else "",
            "next_verification": v.next_verification_date, "method": v.method.designation if v.method else "",
        })
    if not rows:
        return ToolResult("В журнале EI_doc ничего не найдено.")
    return ToolResult(_dump(rows), {"sources": [_source("ei", "Журнал поверок EI_doc")]})


def ei_reference(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.catalog.models import MeasurementFamily, Standard, VerificationMethod
    from apps.core.models import Employee

    what = args.get("what")
    if what == "families":
        rows = list(MeasurementFamily.objects.values("id", "code", "name", "calculator_key"))
    elif what == "standards":
        today = dt.date.today()
        rows = [
            {"id": s.pk, "name": s.name, "fif_number": s.fif_number, "serial_number": s.serial_number,
             "verified_at": s.verified_at, "valid_until": s.valid_until, "valid_today": s.valid_until >= today,
             "location": s.location, "in_use": s.is_active}
            for s in Standard.objects.all()
        ]
    elif what == "methods":
        rows = [
            {"id": m.pk, "designation": m.designation, "name": m.name,
             "families": [f.name for f in m.families.all()], "has_document": bool(m.document)}
            for m in VerificationMethod.objects.prefetch_related("families")
        ]
    elif what == "employees":
        rows = [
            {"id": e.pk, "full_name": e.full_name, "short_name": e.short_name, "position": e.position,
             "tab_number": e.tab_number}
            for e in Employee.objects.filter(is_active=True)
        ]
    else:
        raise ToolFailure("what: families | standards | methods | employees")
    return ToolResult(_dump(rows) if rows else "Справочник пуст.",
                      {"sources": [_source("ei", f"Справочник EI_doc: {what}")]})


# ===========================================================================
# Аттестация испытательного оборудования
# ===========================================================================
def ei_attestations(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.equipment.calc import PRESETS
    from apps.equipment.expr import FUNCTIONS
    from apps.equipment.models import AttestationProgram, EquipmentAttestation

    what = args.get("what")
    if what == "programs":
        rows = [
            {"id": p.pk, "title": p.title, "equipment_kind": p.equipment_kind, "note": p.note,
             "needs_review": p.needs_review, "plan": p.plan}
            for p in AttestationProgram.objects.filter(is_active=True)
        ]
        label = "Программы аттестации EI_doc"
    elif what == "formulas":
        rows = {
            "functions": {name: desc for name, (_, _, _, desc) in FUNCTIONS.items()},
            "presets": PRESETS,
            "note": "Формулы не ограничены готовыми — пиши свои выражения из этих функций по методике/примеру.",
        }
        label = "Формулы аттестации EI_doc"
    elif what == "attestations":
        qs = EquipmentAttestation.objects.all()
        for word in str(args.get("query") or "").split():
            qs = qs.filter(
                Q(equipment__name__icontains=word) | Q(equipment__serial_number__icontains=word)
                | Q(equipment__owner__icontains=word)
            )
        rows = [
            {"id": a.pk, "number": a.full_number, "status": a.status, "kind": a.kind,
             "protocol_date": a.protocol_date, "suitable": a.suitable, "equipment": a.equipment,
             "commission": a.commission, "instruments": a.instruments, "plan": a.plan, "texts": a.texts}
            for a in qs[: min(int(args.get("limit") or 10), 30)]
        ]
        label = "Аттестации ИО в EI_doc"
    else:
        raise ToolFailure("what: programs | formulas | attestations")
    return ToolResult(_dump(rows) if rows else "Ничего не найдено.", {"sources": [_source("ei", label)]})


def _check_widget(report: dict) -> dict:
    return {
        "type": "table",
        "title": "Сверка формул с примером" + (f" ({report['source']})" if report.get("source") else "")
                 + (" — всё сходится" if report["ok"] else " — есть расхождения"),
        "columns": ["Режим", "Характеристика", "Точка", "В примере", "Посчитано", "Сходится"],
        "rows": [
            [r["mode"], r["label"], r["point"], r["expected"], r["computed"], "да" if r["ok"] else "НЕТ"]
            for r in report["rows"]
        ] + [["", e, "", "", "", "ошибка"] for e in report["errors"]],
    }


def _run_check(plan: dict, example) -> dict:
    from apps.equipment import calc

    try:
        return calc.check_example(plan, example)
    except calc.PlanError as exc:
        raise ToolFailure(f"Сверка не выполнена: {exc}") from exc


def ei_attestation_check(ctx: ToolContext, args: dict) -> ToolResult:
    """Ничего не сохраняет: прогоняет план на числах примера и сравнивает с результатами примера."""
    from apps.equipment import calc

    try:
        plan = calc.clean_plan(args.get("plan"))
    except calc.PlanError as exc:
        raise ToolFailure(f"план: {exc}") from exc
    report = _run_check(plan, args.get("example"))
    lines = [
        f"{r['mode']} / {r['key']}{' / ' + r['point'] if r['point'] else ''}: пример {r['expected']}, "
        f"посчитано {r['computed']} (разница {r['diff']}) — {'сходится' if r['ok'] else 'НЕ сходится'}"
        for r in report["rows"]
    ] + [f"Ошибка: {e}" for e in report["errors"]]
    if report["unchecked"]:
        lines.append("Не с чем сверить (нет результата в примере): " + ", ".join(report["unchecked"]))
    head = ("Все формулы сходятся с примером." if report["ok"] else
            "Есть расхождения: перечитай методику/пример, поправь expr (порядок округлений, какие точки входят, "
            "знак) и сверь снова. Не подгоняй формулу под число без объяснения из документа.")
    return ToolResult(head + "\n" + "\n".join(lines), {"widgets": [_check_widget(report)]})


def ei_attestation_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.equipment import service
    from apps.equipment.models import AttestationProgram

    if "measurements" in args or "conditions" in args:
        raise ToolFailure(
            "Результаты измерений и условия проведения ассистент не передаёт — измерения вносит человек "
            "на экране «Аттестация ИО». Убери measurements/conditions и предложи карточку с планом. "
            "Числа из примера — только в example, для сверки формул."
        )
    if not service.can_edit(ctx.user):
        raise ToolFailure("У этого пользователя нет права заводить аттестации (наблюдатель)")
    try:
        card = service.clean_card(args)
    except service.AttestationError as exc:
        raise ToolFailure(str(exc)) from exc
    sources = [str(s) for s in (args.get("sources") or []) if str(s).strip()]
    if not sources:
        raise ToolFailure(
            "Укажи sources — откуда взяты наименование, заводской номер, изготовитель, год, режимы, формулы и "
            "допуски (пример протокола, РЭ, паспорт, шильдик, прошлый протокол, программа аттестации)"
        )
    program_id = args.get("program_id")
    if program_id and not AttestationProgram.objects.filter(pk=program_id).exists():
        raise ToolFailure(f"Программы аттестации с id {program_id} нет")
    report = _run_check(card["plan"], args["example"]) if args.get("example") else None
    e = card["equipment"]
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="attestation_create",
        title=f"Завести аттестацию: {e['name']} № {e['serial_number']}",
        payload={**card, "program_id": program_id or None, "plan_check": report},
        sources=sources,
    )
    note = (
        "Карточка аттестации с планом показана человеку с кнопкой «Подтвердить». После подтверждения "
        "аттестация появится на вкладке «Аттестация ИО»: там человек вносит измерения и условия, расчёт и "
        "заключение делает EI_doc, план проверяет метролог."
    )
    if report is None:
        note += " Сверки с примером нет — скажи человеку, что формулы метролог сверяет с методикой вручную."
    elif not report["ok"]:
        note += " ВНИМАНИЕ: сверка с примером не сошлась — перечисли человеку расхождения."
    ui = {"action": action.pk}
    if report:
        ui["widgets"] = [_check_widget(report)]
    return ToolResult(note, ui)


def ei_attestation_template_propose(ctx: ToolContext, args: dict) -> ToolResult:
    """Шаблон протокола аттестации для вида ИО — без конкретного оборудования, комиссии и измерений."""
    from apps.equipment import service
    from apps.equipment.models import AttestationProgram

    forbidden = {"measurements", "conditions", "readings", "results", "equipment", "commission"} & set(args)
    if forbidden:
        raise ToolFailure(
            f"Шаблон — только структура протокола: убери {', '.join(sorted(forbidden))}. Оборудование, комиссию, "
            "измерения и условия человек вносит сам, когда заводит аттестацию по шаблону на вкладке «Аттестация ИО»."
        )
    if not service.can_edit(ctx.user):
        raise ToolFailure("У этого пользователя нет права заводить шаблоны (наблюдатель)")
    try:
        card = service.clean_program(args)
    except service.AttestationError as exc:
        raise ToolFailure(str(exc)) from exc
    sources = [str(s) for s in (args.get("sources") or []) if str(s).strip()]
    if not sources:
        raise ToolFailure(
            "Укажи sources — откуда взяты режимы, точки, формулы, допуски и каркас (образец протокола, методика/"
            "программа аттестации с пунктами, ГОСТ, РЭ, карточка типа во ФГИС)"
        )
    program_id = args.get("program_id")
    if program_id:
        if not AttestationProgram.objects.filter(pk=program_id).exists():
            raise ToolFailure(f"Шаблона с id {program_id} нет — посмотри ei_attestations what=programs")
    else:
        same = AttestationProgram.objects.filter(title=card["title"]).first()
        if same:
            raise ToolFailure(f"Шаблон «{card['title']}» уже есть (id {same.pk}) — передай program_id, чтобы "
                              "обновить его, или дай другое название")
    report = _run_check(card["plan"], args["example"]) if args.get("example") else None
    plan = card["plan"]
    view = [
        ["Название", card["title"]],
        ["Вид оборудования", card["equipment_kind"]],
        ["Величина", f"{plan.get('quantity', '')}, {plan.get('unit', '')}".strip(", ")],
        ["Режимы", "; ".join(str(m.get("label") or m.get("key")) for m in plan.get("modes", []))],
        ["Точки", f"{len(plan.get('points', []))} × {plan.get('repeats', '')} повт."],
        ["Характеристики", "; ".join(str(c.get("label") or c.get("key")) for c in plan.get("characteristics", []))],
        ["Документы", "; ".join(plan.get("documents", []))],
        ["Сверка с примером", "нет" if report is None else ("сходится" if report["ok"] else "ЕСТЬ РАСХОЖДЕНИЯ")],
    ]
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="attestation_template_save",
        title=("Обновить" if program_id else "Завести") + f" шаблон аттестации: {card['title']}",
        payload={**card, "program_id": program_id or None, "plan_check": report, "view": view},
        sources=sources,
    )
    note = (
        "Карточка шаблона показана человеку с кнопкой «Подтвердить». После подтверждения шаблон появится в "
        "списке «Новая аттестация» на вкладке «Аттестация ИО»: человек выбирает его, вписывает оборудование, "
        "комиссию и средства измерений, вносит измерения и условия; расчёт, заключение, номер и протокол "
        "(Word/PDF) делает EI_doc. Шаблон от поверителя ждёт проверки метролога."
    )
    if report is None:
        note += " Сверки с примером нет — скажи, что формулы метролог сверит с методикой."
    elif not report["ok"]:
        note += " ВНИМАНИЕ: сверка с примером не сошлась — перечисли расхождения."
    ui = {"action": action.pk}
    if report:
        ui["widgets"] = [_check_widget(report)]
    return ToolResult(note, ui)


# ===========================================================================
# Шаблоны поверки СИ (apps.verification.templates)
# ===========================================================================
def ei_verification_templates(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.verification.models import VerificationTemplate

    qs = VerificationTemplate.objects.select_related("family", "si_type").filter(is_active=True)
    for word in str(args.get("query") or "").split():
        qs = qs.filter(Q(title__icontains=word) | Q(family__name__icontains=word) | Q(si_type__name__icontains=word)
                       | Q(si_type__registry_number__icontains=word))
    rows = [
        {"id": t.pk, "title": t.title, "family": t.family.name, "family_id": t.family_id,
         "si_type": str(t.si_type) if t.si_type_id else "", "needs_review": t.needs_review,
         "sources": t.sources, "plan": t.plan if args.get("full") else {
             k: t.plan.get(k) for k in ("si_kind", "method", "params", "operations")}}
        for t in qs[: min(int(args.get("limit") or 10), 30)]
    ]
    return ToolResult(_dump(rows) if rows else "Шаблонов поверки не найдено.",
                      {"sources": [_source("ei", "Шаблоны поверки EI_doc")]})


def _verification_check(plan: dict, example) -> dict:
    from apps.verification import templates

    try:
        return templates.check_example(plan, example)
    except templates.TemplateError as exc:
        raise ToolFailure(str(exc)) from exc


def ei_verification_template_check(ctx: ToolContext, args: dict) -> ToolResult:
    """Ничего не сохраняет: шаблон + параметры прибора из примера → прогон на числах примера."""
    from apps.verification import templates

    try:
        plan = templates.clean_template(args.get("plan"))
    except templates.TemplateError as exc:
        raise ToolFailure(f"шаблон: {exc}") from exc
    report = _verification_check(plan, args.get("example"))
    lines = [
        f"{r['mode']} / {r['key']}: пример {r['expected']}, посчитано {r['computed']} — "
        f"{'сходится' if r['ok'] else 'НЕ сходится'}" for r in report["rows"]
    ] + [f"Ошибка: {e}" for e in report["errors"]]
    head = "Все формулы сходятся с примером." if report["ok"] else (
        "Есть расхождения: перечитай методику/пример и поправь expr. Не подгоняй формулу под число без объяснения.")
    return ToolResult(head + "\n" + "\n".join(lines), {"widgets": [_check_widget(report)]})


def ei_verification_template_propose(ctx: ToolContext, args: dict) -> ToolResult:
    """Шаблон поверки для семейства/типа СИ: карточка «Подтвердить» + пустой протокол по шаблону (Word/PDF)."""
    from apps.catalog.api_types import can_create
    from apps.catalog.models import MeasurementFamily, SiType
    from apps.verification import templates
    from apps.verification.models import VerificationTemplate

    forbidden = {"measurements", "readings", "results", "instrument", "serial_number", "operations_results"} & set(args)
    if forbidden:
        raise ToolFailure(
            f"Шаблон — только структура: убери {', '.join(sorted(forbidden))}. Прибор, показания и отметки вносит "
            "поверитель, когда проводит поверку по шаблону."
        )
    if not can_create(ctx.user):
        raise ToolFailure("У этого пользователя нет права заводить шаблоны (наблюдатель)")
    try:
        plan = templates.clean_template(args.get("plan"))
    except templates.TemplateError as exc:
        raise ToolFailure(f"шаблон: {exc}") from exc
    family = MeasurementFamily.objects.filter(pk=args.get("family")).first()
    if family is None:
        raise ToolFailure("family — id семейства СИ (ei_reference what=families). Нет подходящего — скажи человеку, "
                          "что метролог заводит семейство в справочнике.")
    si_type = None
    if args.get("si_type"):
        si_type = SiType.objects.filter(pk=args["si_type"]).first()
        if si_type is None or si_type.family_id != family.pk:
            raise ToolFailure("si_type — id типа из справочника EI_doc (ei_si_types) того же семейства, или не указывай")
    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolFailure("title — название шаблона («Манометры показывающие — МИ 2124-90»)")
    template_id = args.get("template_id")
    if template_id:
        if not VerificationTemplate.objects.filter(pk=template_id).exists():
            raise ToolFailure(f"Шаблона поверки {template_id} нет — посмотри ei_verification_templates")
    elif VerificationTemplate.objects.filter(title=title).exists():
        raise ToolFailure(f"Шаблон «{title}» уже есть — передай template_id, чтобы обновить, или назови иначе")
    sources = [str(x) for x in (args.get("sources") or []) if str(x).strip()]
    if not sources:
        raise ToolFailure("Укажи sources — методика поверки (пункты), описание типа, ГОСТ: откуда точки, формулы и допуски")
    report = _verification_check(plan, args["example"]) if args.get("example") else None
    kits = _kits(args.get("kits"))

    view = [
        ["Название", title], ["Семейство", family.name], ["Тип СИ", str(si_type) if si_type else "все типы семейства"],
        ["Методика", plan["method"]],
        ["Параметры прибора", "; ".join(
            p["label"] + (" (из типа)" if p["from_type"] else " (вносит поверитель)") for p in plan["params"]) or "—"],
        ["Операции", "; ".join(op["label"] for op in plan["operations"])],
        ["Поверяемые точки", "; ".join(str(m["label"] or m["setpoint"]) for m in plan["modes"])],
        ["Величины в точке", "; ".join(str(p.get("label") or p["key"]) for p in plan["points"])
         + f" × {plan['repeats']}"],
        ["Характеристики", "; ".join(str(c.get("label")) for c in plan["characteristics"])],
        ["Наборы эталонов", "; ".join(str(k) for k in kits) or "не выбраны"],
        ["Сверка с примером", "нет" if report is None else ("сходится" if report["ok"] else "ЕСТЬ РАСХОЖДЕНИЯ")],
    ]
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="verification_template_save",
        title=("Обновить" if template_id else "Завести") + f" шаблон поверки: {title}",
        payload={"title": title, "family": family.pk, "si_type": si_type.pk if si_type else None, "plan": plan,
                 "note": str(args.get("note") or "")[:2000], "template_id": template_id or None,
                 "plan_check": report, "view": view, **({"kits": [k.pk for k in kits]} if kits else {})},
        sources=sources,
    )
    files = []
    spec = templates.preview_spec(plan, title=title)
    for fmt in ("docx", "pdf"):
        try:
            data = templates.render(spec, fmt)
        except Exception:  # noqa: BLE001 — нет typst: обойдёмся Word
            logger.warning("Пустой протокол по шаблону в %s не собрался", fmt, exc_info=True)
            continue
        attachment = _save_file(ctx, documents.safe_filename(f"Шаблон протокола — {title}", fmt), data, MIME[fmt])
        files.append({"id": attachment.pk, "name": attachment.name, "size": attachment.size, "mime": attachment.mime})
    note = (
        "Карточка шаблона показана человеку с кнопкой «Подтвердить», рядом — пустой протокол по шаблону (так он "
        "будет выглядеть). После подтверждения поверитель на экране «Поверки» выбирает прибор этого семейства/типа, "
        "вносит параметры прибора (если их нет в типе), отметки по операциям и показания; расчёт, годность и "
        "протокол (Word/PDF) делает EI_doc, протокол виден в журнале. Шаблон от поверителя ждёт проверки "
        "метролога — протокол по нему не подпишется до проверки."
    )
    missing_type = [p["label"] for p in plan["params"] if p["from_type"] and si_type
                    and (si_type.limits or {}).get(p["from_type"]) in (None, "")]
    if missing_type:
        note += f" У типа «{si_type}» в справочнике нет характеристик: {', '.join(missing_type)} — скажи человеку."
    if report is None:
        note += " Сверки с примером нет — скажи, что формулы метролог сверит с методикой."
    elif not report["ok"]:
        note += " ВНИМАНИЕ: сверка с примером не сошлась — перечисли расхождения."
    ui = {"action": action.pk, "files": files}
    if report:
        ui["widgets"] = [_check_widget(report)]
    return ToolResult(note, ui)


def _kits(raw) -> list:
    """id строк «Перечня применяемых эталонов» → строки (только действующие)."""
    from apps.catalog.models import StandardKit

    if not raw:
        return []
    try:
        ids = [int(x) for x in raw]
    except (TypeError, ValueError) as exc:
        raise ToolFailure("kits — список id строк перечня эталонов (ei_standard_kits)") from exc
    kits = list(StandardKit.objects.filter(pk__in=ids, is_active=True))
    if len(kits) != len(set(ids)):
        raise ToolFailure("kits: часть id не найдена или строка выключена — посмотри ei_standard_kits")
    return kits


# ===========================================================================
# Перечень применяемых эталонов (apps.catalog.standard_kits)
# ===========================================================================
def ei_standard_kits(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.catalog import standard_kits
    from apps.catalog.models import AuxInstrument

    qs = standard_kits.search_kits(args.get("query") or "", family=args.get("family") or None,
                                   active_only=not args.get("all"))
    rows = [{k: v for k, v in standard_kits.kit_dict(kit).items() if k not in ("updated_at", "sort")}
            for kit in qs[: min(int(args.get("limit") or 40), 120)]]
    payload = {"kits": rows}
    if args.get("with_aux") or not str(args.get("query") or "").strip():
        payload["aux_instruments"] = [standard_kits.aux_dict(a) for a in AuxInstrument.objects.all()
                                      if a.is_active or args.get("all")]
    if not rows and not payload.get("aux_instruments"):
        return ToolResult("В перечне эталонов ничего не нашлось (попробуй другое слово: «весы», «манометр»).")
    return ToolResult(_dump(payload), {"sources": [_source("ei", "Перечень применяемых эталонов EI_doc")]})


def ei_standard_kits_propose(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.catalog import standard_kits
    from apps.catalog.api_types import is_reviewer

    if not is_reviewer(ctx.user):
        raise ToolFailure("Перечень эталонов меняет метролог или руководитель — предложи человеку попросить их")
    changes = args.get("changes")
    # Проверка «на пробу»: те же правки в откатываемой транзакции — ошибку модель увидит сразу, до карточки.
    from django.db import transaction

    class _Dry(Exception):
        pass

    try:
        with transaction.atomic():
            done = standard_kits.apply_changes(changes, ctx.user)
            raise _Dry
    except _Dry:
        pass
    except standard_kits.KitError as exc:
        raise ToolFailure(str(exc)) from exc
    view = [[f"{i}.", line] for i, line in enumerate(done, start=1)]
    action = PendingAction.objects.create(
        conversation=ctx.conversation, kind="standard_kits_update",
        title=f"Обновить перечень эталонов: {len(done)} правк{'а' if len(done) == 1 else 'и'}",
        payload={"changes": changes, "view": view},
        sources=[str(x) for x in (args.get("sources") or []) if str(x).strip()],
    )
    return ToolResult("Карточка с правками перечня показана человеку с кнопкой «Подтвердить».", {"action": action.pk})


def ei_fif_document_read(ctx: ToolContext, args: dict) -> ToolResult:
    """Текст методики поверки / описания типа из документов, скачанных EI_doc из ФГИС."""
    from apps.arshin import documents as fif_documents
    from apps.arshin.models import FifDocument
    from apps.catalog.models import SiType

    mit_uuid = str(args.get("mit_uuid") or "").strip()
    if args.get("si_type"):
        si_type = SiType.objects.filter(pk=args["si_type"]).first()
        if si_type is None:
            raise ToolFailure(f"Типа СИ {args['si_type']} нет в справочнике")
        mit_uuid = si_type.fif_uuid
    if not mit_uuid:
        raise ToolFailure("Нужен si_type (id в EI_doc) с привязкой к ФГИС или mit_uuid")
    docs = list(FifDocument.objects.filter(mit_uuid=mit_uuid))
    if not docs:
        docs = fif_documents.fetch_type_documents(mit_uuid)
    kind = args.get("kind") or "meth"
    chosen = [d for d in docs if d.kind == kind] or docs
    if args.get("document_id"):
        chosen = [d for d in docs if d.pk == args["document_id"]]
    if not chosen:
        return ToolResult("У типа в ФГИС нет документов. Обозначение методики — из get_type, текст — web_fetch/поиск.")
    doc = chosen[0]
    if not doc.file:
        doc = fif_documents.download(doc)
    listing = "; ".join(f"[{d.pk}] {d.get_kind_display()}: {d.title}" for d in docs)
    if not doc.file:
        return ToolResult(f"Документ «{doc.title}» не скачан ({doc.error or 'ФГИС не ответила'}). Ссылка: {doc.url}. "
                          f"Все документы: {listing}")
    with doc.file.open("rb") as fh:
        content = fh.read()
    text = _pdf_text(content)
    if len(text.strip()) < 200:
        return ToolResult(f"«{doc.title}» — скан без текстового слоя, прочитать текстом нельзя. Ссылка: {doc.url}. "
                          f"Все документы: {listing}")
    part, tail = _slice(text, args.get("offset") or 0, args.get("max_chars") or 30000)
    head = f"{doc.get_kind_display()}: «{doc.title}» (документ [{doc.pk}]). Все документы: {listing}\n\n"
    return ToolResult(head + part + tail, {"sources": [_source("fif", doc.title, url=doc.url)]})


def _pdf_text(content: bytes) -> str:
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(content)
    except Exception:  # noqa: BLE001 — не PDF: пробуем как текст/Word ниже
        return documents_text(content)
    try:
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            pages.append(f"--- страница {index + 1} ---\n{textpage.get_text_range() or ''}")
            textpage.close()
            page.close()
        return "\n".join(pages)
    finally:
        pdf.close()


def documents_text(content: bytes) -> str:
    try:
        import io

        import docx

        return "\n".join(p.text for p in docx.Document(io.BytesIO(content)).paragraphs)
    except Exception:  # noqa: BLE001
        return ""


# ===========================================================================
# Виджеты
# ===========================================================================
WIDGET_TYPES = ("table", "cards", "kv", "chart", "steps", "choices", "callout", "html", "compare")


def ui_show(ctx: ToolContext, args: dict) -> ToolResult:
    widget = args.get("widget") or {k: v for k, v in args.items()}
    if not isinstance(widget, dict) or widget.get("type") not in WIDGET_TYPES:
        raise ToolFailure(f"widget.type должен быть одним из: {', '.join(WIDGET_TYPES)}")
    raw = _dump(widget)
    if len(raw) > 200_000:
        raise ToolFailure("Виджет слишком большой — сократи данные")
    if widget["type"] == "html" and not widget.get("html"):
        raise ToolFailure("Для html-виджета нужен html")
    return ToolResult("Виджет показан человеку.", {"widgets": [widget]})


# ===========================================================================
# Описание встроенных инструментов
# ===========================================================================
CATEGORIES = [c.value for c in KnowledgeCategory]
STR = {"type": "string"}
INT = {"type": "integer"}

BLOCKS_SCHEMA = {
    "type": "array",
    "description": (
        "Блоки документа по порядку. type: heading{text,level 1-3} | paragraph{text,align left|center|right|justify,bold,italic,size} | "
        "fields{rows:[{label,value,note}]} — строки «подпись: значение» с подчёркиванием и мелкой подписью под значением, как в бланках протоколов | "
        "table{columns:[...],rows:[[...]],widths?:[относительные ширины]} | list{items,ordered} | "
        "signatures{rows:[{role,name}]} | pagebreak | spacer{size}"
    ),
    "items": {"type": "object"},
}

DOC_PROPS = {
    "title": STR,
    "subtitle": STR,
    "header": {
        "description": "'org' — шапка ООО «Единица Измерения»; или список строк; или не указывать",
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
    },
    "blocks": BLOCKS_SCHEMA,
    "filename": {"type": "string", "description": "Имя файла без расширения"},
}

BUILTIN: list[Tool] = [
    Tool(
        "context_search", "Ищу во внутренних файлах компании",
        "Полнотекстовый поиск по внутренним файлам компании (регламенты, методики поверки, описания типов, "
        "свидетельства эталонов, образцы протоколов). Возвращает фрагменты с file_id. Используй ПЕРВЫМ для любых "
        "вопросов о правилах компании и методиках. Ищи по номерам документов и ключевым словам.",
        _obj({"query": STR, "category": {"type": "string", "enum": CATEGORIES}, "limit": INT}, ["query"]),
        context_search,
    ),
    Tool(
        "context_read", "Читаю файл компании",
        "Прочитать внутренний файл целиком или по частям (offset/max_chars).",
        _obj({"file_id": INT, "offset": INT, "max_chars": INT}, ["file_id"]),
        context_read,
    ),
    Tool(
        "memory_save", "Запоминаю",
        "Сохранить устойчивый факт в постоянную память. scope=personal — личная заметка этого сотрудника "
        "(сохраняется сразу); scope=company — правило/факт для всей компании (требует подтверждения человеком). "
        "Сохраняй только проверенные, долгоживущие факты, не ход текущей задачи.",
        _obj({"text": STR, "scope": {"type": "string", "enum": ["personal", "company"]}}, ["text"]),
        memory_save,
    ),
    Tool(
        "memory_delete", "Удаляю из памяти",
        "Удалить запись памяти по id (id видны в системном промте).",
        _obj({"memory_id": INT}, ["memory_id"]),
        memory_delete,
    ),
    Tool(
        "web_search", "Ищу в интернете",
        "Поиск в интернете (Яндекс). Для документации на приборы, методик поверки (МП, МИ), ГОСТ, описаний типов, "
        "сайтов изготовителей. Затем открывай нужные ссылки web_fetch — сниппетам не доверяй.",
        _obj({"query": STR, "page": INT}, ["query"]),
        web_search,
    ),
    Tool(
        "web_fetch", "Читаю страницу",
        "Открыть страницу или документ (HTML, PDF, DOCX) по ссылке и получить текст. Длинные документы — по частям через offset.",
        _obj({"url": STR, "offset": INT, "max_chars": INT}, ["url"]),
        web_fetch,
    ),
    Tool(
        "attachment_read", "Читаю приложенный файл",
        "Прочитать текст файла, приложенного человеком к чату (PDF, Word, Excel, CSV, TXT), по attachment_id.",
        _obj({"attachment_id": INT, "offset": INT, "max_chars": INT}, ["attachment_id"]),
        attachment_read,
    ),
    Tool(
        "file_create", "Создаю файл",
        "Создать файл для человека: docx/pdf из blocks; xlsx/csv из sheets [{name, columns, rows}] "
        "(в ячейках можно формулы '=A2*2'); md/txt из content. formats — можно несколько сразу (['docx','pdf']).",
        _obj({
            **DOC_PROPS,
            "formats": {"type": "array", "items": {"type": "string", "enum": list(MIME)}},
            "draft": {"type": "boolean"},
            "sheets": {"type": "array", "items": {"type": "object"}},
            "content": STR,
        }, ["formats", "filename"]),
        file_create,
    ),
    Tool(
        "protocol_draft", "Составляю черновик протокола",
        "Собрать черновик протокола с ДАННЫМИ конкретного прибора (только если человек их дал) — Word + PDF с "
        "пометкой ЧЕРНОВИК. Шаблон протокола без данных — НЕ здесь: ei_verification_template_propose (поверка СИ) "
        "или ei_attestation_template_propose (аттестация ИО). "
        "Структуру повторяй по образцу (из файлов компании или приложенного человеком). Неизвестные значения — "
        "«________», никогда не выдумывай. facts — ОБЯЗАТЕЛЬНАЯ таблица происхождения КАЖДОГО существенного "
        "значения: {field, value, source (откуда: «ФГИС Аршин, поверка vri 1-…», «методика МП …, п. 5», "
        "«со слов пользователя», «описание типа, табл. 2»), url, status: confirmed (взято из первоисточника) | "
        "derived (вычислено/выведено — в source укажи как) | missing (нет данных)}.",
        _obj({
            **DOC_PROPS,
            "facts": {"type": "array", "items": {"type": "object"}},
        }, ["title", "blocks", "facts"]),
        protocol_draft,
    ),
    Tool(
        "ei_si_types", "Смотрю справочник типов СИ",
        "Поиск в справочнике типов СИ EI_doc (уже заведённые типы, с характеристиками limits).",
        _obj({"query": STR, "limit": INT}),
        ei_si_types,
    ),
    Tool(
        "ei_si_type_propose", "Готовлю новый тип СИ",
        "Предложить завести новый тип СИ в справочник EI_doc. Человек увидит карточку и подтвердит. "
        "Перед этим ОБЯЗАТЕЛЬНО: найти тип в ФГИС (номер, наименование, изготовитель, МПИ), проверить, что в "
        "справочнике его нет (ei_si_types), взять характеристики из описания типа. Для счётчиков воды limits: "
        "q_min, q_transition_b, q_transition_a, q_nominal, q_max (м³/ч), error_below_transition, "
        "error_above_transition (±%). family — id семейства (ei_reference what=families). "
        "sources — список строк, откуда взято каждое значение.",
        _obj({
            "registry_number": STR, "name": STR, "manufacturer": STR, "verification_interval_months": INT,
            "family": INT, "limits": {"type": "object"}, "fif_url": STR,
            "sources": {"type": "array", "items": STR},
        }, ["registry_number", "name", "manufacturer", "sources"]),
        ei_si_type_propose,
    ),
    Tool(
        "ei_journal", "Смотрю журнал поверок",
        "Поиск в собственном журнале поверок EI_doc: по заводскому номеру, номеру типа, словам (тип, изготовитель, "
        "владелец, адрес), периоду. Полезно для «соседей»: что ещё поверяли у этого владельца/по этому адресу.",
        _obj({"query": STR, "serial_number": STR, "registry_number": STR, "date_from": STR, "date_to": STR, "limit": INT}),
        ei_journal,
    ),
    Tool(
        "ei_reference", "Смотрю справочники EI_doc",
        "Справочники EI_doc: families (семейства СИ), standards (эталоны компании со сроками поверки), "
        "methods (методики поверки), employees (сотрудники).",
        _obj({"what": {"type": "string", "enum": ["families", "standards", "methods", "employees"]}}, ["what"]),
        ei_reference,
    ),
    Tool(
        "ei_attestations", "Смотрю аттестации ИО",
        "Аттестация испытательного оборудования в EI_doc: what=programs — заготовки планов по видам ИО; "
        "formulas — функции для формул и готовые формулы-подсказки; attestations — уже заведённые аттестации "
        "(query — наименование, заводской номер, владелец): прошлый протокол того же ИО — источник карточки, "
        "комиссии, плана и каркаса.",
        _obj({"what": {"type": "string", "enum": ["programs", "formulas", "attestations"]}, "query": STR, "limit": INT},
             ["what"]),
        ei_attestations,
    ),
    Tool(
        "ei_attestation_check", "Сверяю формулы с примером",
        "Прогнать план на числах из ПРИМЕРА протокола и сравнить с результатами, напечатанными в примере. Ничего "
        "не сохраняет. example{source: «откуда пример», modes: {ключ_режима: {display?, readings: {ключ_точки: "
        "[отсчёты как в примере]}, expected: {ключ_характеристики: «значение как напечатано» | {ключ_точки: …}}}}}. "
        "Сходится в пределах единицы последнего напечатанного знака. Используй перед ei_attestation_propose всякий "
        "раз, когда в примере есть и отсчёты, и результаты. " + plan_help(),
        _obj({"plan": {"type": "object"}, "example": {"type": "object"}}, ["plan", "example"]),
        ei_attestation_check,
    ),
    Tool(
        "ei_attestation_propose", "Готовлю карточку аттестации ИО",
        "Предложить завести аттестацию испытательного оборудования (карточка + план + каркас протокола). Человек "
        "подтвердит кнопкой. Ты собираешь структуру: оборудование, комиссию, средства измерений, план (точки, "
        "режимы, формулы, допуски с источниками, каркас протокола по примеру), тексты. Результаты измерений и "
        "условия НЕ передаются — их вносит человек на экране; расчёт и заключение делает EI_doc. "
        "equipment{name, serial_number, manufacturer, year, owner, location, inventory_number}; "
        "kind: primary|periodic|repeat; commission[{role: chair|member, position, name}]; "
        "instruments[{name, serial_number, registry_number, characteristics, verification, valid_until YYYY-MM-DD}] — "
        "свидетельства и сроки найди во ФГИС; texts{documentation, inspection, testing, notes, recommendations}; "
        "example — то же, что в ei_attestation_check (отчёт сверки сохранится в карточке для метролога). "
        "sources — откуда каждое значение карточки, каждая формула и допуск.",
        _obj({
            "kind": STR, "equipment": {"type": "object"}, "commission": {"type": "array", "items": {"type": "object"}},
            "instruments": {"type": "array", "items": {"type": "object"}}, "plan": {"type": "object"},
            "texts": {"type": "object"}, "program_id": INT, "example": {"type": "object"},
            "sources": {"type": "array", "items": STR},
        }, ["equipment", "plan", "sources"]),
        ei_attestation_propose,
    ),
    Tool(
        "ei_attestation_template_propose", "Готовлю шаблон протокола аттестации",
        "ГЛАВНЫЙ способ «составить протокол аттестации по образцу/методике»: завести ШАБЛОН (программу-заготовку) "
        "для вида ИО — план (точки, режимы, формулы, допуски с источниками) и каркас протокола (layout, document, "
        "conclusion) по образцу. Без конкретного оборудования, комиссии, измерений и условий — их человек вносит "
        "сам, когда заводит аттестацию по шаблону. Человек подтвердит кнопкой. title — название шаблона "
        "(«Камеры климатические — периодическая аттестация»), equipment_kind, plan, note, program_id — чтобы "
        "обновить существующий шаблон, example — как в ei_attestation_check, sources — откуда каждая формула, "
        "допуск и раздел каркаса. " + plan_help(),
        _obj({
            "title": STR, "equipment_kind": STR, "plan": {"type": "object"}, "note": STR, "program_id": INT,
            "example": {"type": "object"}, "sources": {"type": "array", "items": STR},
        }, ["title", "equipment_kind", "plan", "sources"]),
        ei_attestation_template_propose,
    ),
    Tool(
        "ei_verification_templates", "Смотрю шаблоны поверки",
        "Шаблоны поверки СИ в EI_doc (по семейству/типу): query — слова названия, семейства, типа или номер в "
        "реестре; full=true — целиком (чтобы обновить через template_id).",
        _obj({"query": STR, "full": {"type": "boolean"}, "limit": INT}),
        ei_verification_templates,
    ),
    Tool(
        "ei_verification_template_check", "Сверяю шаблон поверки с примером",
        "Прогнать шаблон поверки на числах ПРИМЕРА протокола и сравнить с напечатанными результатами. Ничего не "
        "сохраняет. example{source, params{ключ: значение параметра прибора из примера}, modes{ключ_точки: "
        "{readings{ключ_величины: [отсчёты]}, expected{ключ_характеристики: «как напечатано»}}}}.",
        _obj({"plan": {"type": "object"}, "example": {"type": "object"}}, ["plan", "example"]),
        ei_verification_template_check,
    ),
    Tool(
        "ei_verification_template_propose", "Готовлю шаблон протокола поверки",
        "ГЛАВНЫЙ способ сделать «протокол поверки по образцу/методике» для СИ (кроме счётчиков воды — у них свой "
        "протокол): завести ШАБЛОН поверки для семейства (family — id из ei_reference what=families) или "
        "конкретного типа (si_type — id из ei_si_types). Без конкретного прибора и измерений. Человек подтвердит "
        "кнопкой; рядом покажется пустой протокол по шаблону. title, plan, note, template_id (обновить), example "
        "(как в ei_verification_template_check), sources — методика поверки с пунктами, описание типа. "
        + verification_template_help(),
        _obj({
            "title": STR, "family": INT, "si_type": INT, "plan": {"type": "object"}, "note": STR, "template_id": INT,
            "example": {"type": "object"}, "sources": {"type": "array", "items": STR},
            "kits": {"type": "array", "items": INT,
                     "description": "id строк «Перечня применяемых эталонов» (ei_standard_kits) для этой поверки"},
        }, ["title", "family", "plan", "sources"]),
        ei_verification_template_propose,
    ),
    Tool(
        "ei_standard_kits", "Смотрю перечень эталонов",
        "«Перечень применяемых эталонов» компании: строки по видам поверки (раздел, класс/диапазон, эталоны, "
        "вспомогательные СИ, обозначение для столбца U журнала «Номер в реестре^заводской^инвентарный», номера "
        "эталонов в ФИФ ОЕИ, закреплён ли за поверителем) и список вспомогательных СИ. query — слова (вид СИ, "
        "эталон, диапазон), family — id семейства, all=true — с выключенными, with_aux — вспомогательные СИ.",
        _obj({"query": STR, "family": INT, "all": {"type": "boolean"}, "with_aux": {"type": "boolean"},
              "limit": INT}),
        ei_standard_kits,
    ),
    Tool(
        "ei_standard_kits_propose", "Готовлю правку перечня эталонов",
        "Изменить «Перечень применяемых эталонов» (метролог/руководитель; человек подтвердит карточкой). "
        "Сначала ei_standard_kits — найди id строк. changes — список правок, применяются вместе: "
        "{op: add|update|remove, target: kit|aux, id (для update/remove), …поля}. Поля строки (kit): section, "
        "scope, standards_text, aux_text, journal_aux («69551-17^871455^|2231-72^8787^|»), fif_numbers "
        "([«68984.17.4Р.01152950»]), families ([id]), employee (id сотрудника или null), note, is_active. "
        "Поля вспомогательного СИ (aux): name, designation («299-11^22^»), note, is_active. remove — выключает "
        "(история остаётся). Новый эталон в несколько видов поверки — update каждой строки, где он нужен. "
        "Номера и обозначения переписывай символ в символ из слов человека.",
        _obj({"changes": {"type": "array", "items": {"type": "object"}}, "sources": {"type": "array", "items": STR}},
             ["changes"]),
        ei_standard_kits_propose,
    ),
    Tool(
        "ei_fif_document_read", "Читаю методику поверки",
        "Текст документа утверждённого типа (методика поверки kind=meth или описание типа kind=spec), который "
        "EI_doc скачал из ФГИС. si_type — id типа в EI_doc (или mit_uuid), document_id — конкретный документ из "
        "списка, offset/max_chars — для длинных. Сканы без текста не читаются — тогда ссылка.",
        _obj({"si_type": INT, "mit_uuid": STR, "kind": {"type": "string", "enum": ["meth", "spec"]},
              "document_id": INT, "offset": INT, "max_chars": INT}),
        ei_fif_document_read,
    ),
    Tool(
        "ui_show", "Показываю виджет",
        "Показать человеку интерактивный виджет в чате. widget.type: "
        "table{title,columns,rows} | cards{title,items:[{title,subtitle,badge,fields:{k:v},url}]} | "
        "kv{title,rows:[{label,value,source}]} | chart{title,kind:bar|line,labels,series:[{name,values}],unit} | "
        "steps{title,items:[{title,status:done|active|todo|fail,detail}]} | "
        "choices{question,options:[строки]} — кнопки, нажатие отправит текст варианта | "
        "callout{tone:info|warn|error|ok,title,text} | compare{title,columns,rows:[{label,values:[...],diff:bool}]} | "
        "html{title,html,height} — произвольный HTML+JS в песочнице (графики, калькуляторы). "
        "Используй, когда таблица/карточки понятнее текста: кандидаты типов СИ, история поверок, сравнения.",
        _obj({"widget": {"type": "object"}}, ["widget"]),
        ui_show,
    ),
]


# ===========================================================================
# MCP
# ===========================================================================
def _mcp_handler(server: McpServer, tool: str):
    def handler(ctx: ToolContext, args: dict) -> ToolResult:
        try:
            text, is_error = mcp_client.call(server, tool, args)
        except mcp_client.McpError as exc:
            raise ToolFailure(str(exc)) from exc
        source = _source("rst" if server.name == "rst" else "mcp", f"{server} · {tool}", ref=_dump(args)[:300])
        return ToolResult(text or "(пустой ответ)", {"sources": [source]}, is_error=is_error)
    return handler


MCP_LABELS = {
    "search_types": "Ищу тип в реестре ФГИС «Аршин»",
    "get_type": "Открываю карточку типа в ФГИС",
    "search_verifications": "Ищу поверки в ФГИС «Аршин»",
    "get_verification": "Открываю карточку поверки в ФГИС",
    "check_instrument": "Проверяю поверку прибора в ФГИС",
}


def mcp_tools(servers=None) -> tuple[list[Tool], list[str]]:
    """Инструменты всех включённых MCP-серверов. Второй элемент — ошибки подключения."""
    tools, errors = [], []
    for server in servers if servers is not None else McpServer.objects.filter(enabled=True):
        try:
            listed = mcp_client.server_tools(server)
        except mcp_client.McpError as exc:
            errors.append(f"{server}: {exc}")
            continue
        for item in listed:
            if item.get("name") in (server.disabled_tools or []):
                continue
            schema = item.get("inputSchema") or {"type": "object", "properties": {}}
            tools.append(Tool(
                name=mcp_client.tool_name(server.name, item["name"]),
                label=MCP_LABELS.get(item["name"], f"{server}: {item.get('title') or item['name']}"),
                description=(item.get("description") or "")[:1500],
                parameters=schema,
                handler=_mcp_handler(server, item["name"]),
                group=server.name,
            ))
    return tools, errors


def builtin_tools() -> list[Tool]:
    """Встроенные инструменты + личный кабинет (apps.assistant.cabinet импортирует этот модуль, поэтому лениво)."""
    from apps.assistant.cabinet import TOOLS as cabinet_tools

    return [*BUILTIN, *cabinet_tools]


def all_tools(*, with_mcp: bool = True) -> tuple[dict[str, Tool], list[str]]:
    tools = {tool.name: tool for tool in builtin_tools()}
    errors: list[str] = []
    if not web.search_configured():
        tools.pop("web_search", None)
    if with_mcp:
        extra, errors = mcp_tools()
        tools.update({tool.name: tool for tool in extra})
    return tools, errors


def has_context_files(user) -> bool:
    return knowledge.visible_files(user).exclude(mode=ContextMode.OFF).exists()
