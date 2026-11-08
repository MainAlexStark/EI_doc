"""EI_doc как MCP-сервер хоста для агента на agentkit.

Агент (отдельный сервис ``agent/``) ходит сюда с сервисным токеном (``AGENT_HOST_TOKEN``)
и заголовками ``X-Agent-User-Id`` / ``X-Agent-Session-Id``: список инструментов, их
выполнение и инструкции (правила компании, общая память, файлы «Всегда» и оглавление
файлов «Поиск») — от имени этого пользователя и с его правами. Заголовкам верим только
вместе с токеном: без него любой в сети представился бы чужим сотрудником.

Инструменты — те же, что у встроенного ассистента (``tools.builtin_tools``), с теми же
обработчиками. Отличается только то, как подтверждается изменение данных компании:

* ``*_propose`` для агента — изменяющие инструменты с ``_meta["agentkit/preview"]``.
  Сначала агент зовёт их «на пробу» (``params._meta["agentkit/dryRun"]``): обработчик
  проверяет права и данные и заводит ``PendingAction`` — внутри транзакции, которая
  откатывается; строки его карточки (``payload.view``, источники) уходят агенту, и он
  показывает их человеку. После «Подтвердить» тот же вызов идёт по-настоящему:
  обработчик заводит действие, ``actions.decide`` выполняет его от имени человека и
  заново проверяет права и состояние — ровно как кнопка в старом чате.
* Файлы, которые собрал EI_doc (``file_create``, ``protocol_draft``), отдаются агенту
  содержимым (``resource`` с ``blob``) — человек скачивает их прямо из чата агента.

Чего агенту отсюда не нужно, потому что это есть в agentkit: личная память
(``memory_save``), вложения чата (``attachment_read``), виджеты (``ui_show``),
``web_fetch``. Веб-поиск Яндекса — под именем ``yandex_search``.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from urllib.parse import unquote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import APIException

from apps.assistant import actions, prompt, web
from apps.assistant.models import Attachment, Conversation, MemoryItem, PendingAction, Scope
from apps.assistant.tools import STR, Tool, ToolContext, ToolFailure, ToolResult, _kits, _obj, builtin_tools

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
PREVIEW_META = "agentkit/preview"
DRY_RUN_META = "agentkit/dryRun"
MAX_FILE_BYTES = 30 * 1024 * 1024

# Есть в самом agentkit — свои версии здесь только мешали бы (два «запомни», два чтения вложений).
SKIP = {"memory_save", "memory_delete", "attachment_read", "ui_show", "web_fetch"}
RENAME = {"web_search": "yandex_search"}
# Поля payload, которые в карточке не читаются как строка (план, пример сверки): только объём.
BULKY = {"plan", "example", "plan_check", "layout", "document", "conclusion", "blocks"}
LABELS = {
    "registry_number": "Номер в Госреестре", "name": "Наименование", "manufacturer": "Изготовитель",
    "verification_interval_months": "МПИ, мес.", "family": "Семейство (id)", "fif_url": "ФГИС",
    "limits": "Характеристики", "text": "Текст", "title": "Название", "equipment_kind": "Вид ИО", "note": "Примечание",
}


# ── инструменты, которых не было в старом чате ─────────────────────────

def _check_company_memory(user) -> None:
    # До карточки, а не только в исполнителе: иначе поверитель подтвердил бы то, что всё равно не выполнится.
    if not actions._can_edit_company_memory(user):
        raise ToolFailure("Общую память компании меняет метролог или руководитель — предложи человеку попросить их")


def company_memory_save(ctx: ToolContext, args: dict) -> ToolResult:
    _check_company_memory(ctx.user)
    text = (args.get("text") or "").strip()
    if len(text) < 3:
        raise ToolFailure("Пустая запись")
    action = PendingAction.objects.create(conversation=ctx.conversation, kind="memory_company_save",
                                          title="Запомнить для всей компании", payload={"text": text})
    return ToolResult("Ждёт подтверждения.", {"action": action.pk})


def company_memory_delete(ctx: ToolContext, args: dict) -> ToolResult:
    _check_company_memory(ctx.user)
    item = MemoryItem.objects.filter(pk=args.get("memory_id") or 0, scope=Scope.COMPANY).first()
    if item is None:
        raise ToolFailure("Нет такой записи в общей памяти компании (id — в разделе «Общая память компании»)")
    action = PendingAction.objects.create(conversation=ctx.conversation, kind="memory_company_delete",
                                          title="Удалить из общей памяти",
                                          payload={"memory_id": item.pk, "text": item.text})
    return ToolResult("Ждёт подтверждения.", {"action": action.pk})


def template_job_save(ctx: ToolContext, args: dict) -> ToolResult:
    """Черновик шаблона по фоновой работе EI_doc — без карточки: его всё равно проверяет метролог.

    Писать можно только в открытую работу, которую поставил сам EI_doc от имени этого же сотрудника, —
    так агент не может сохранить что-то «мимо» проверки.
    """
    from apps.assistant.models import TemplateJob
    from apps.equipment import service
    from apps.verification import templates

    job = TemplateJob.objects.select_related("si_type").filter(pk=args.get("job_id") or 0).first()
    if job is None or job.status != TemplateJob.Status.RUNNING or job.requested_by_id != ctx.user.pk:
        raise ToolFailure("Нет такой фоновой работы в процессе. Этот инструмент — только для задач «ФОНОВАЯ РАБОТА "
                          "EI_doc»; в обычном разговоре — ei_verification_template_propose / "
                          "ei_attestation_template_propose")
    kind = args.get("kind")
    sources = [str(x) for x in (args.get("sources") or []) if str(x).strip()]
    if not sources:
        raise ToolFailure("Укажи sources — методика поверки с пунктами, описание типа, перечень эталонов")
    if kind == "verification":
        if job.si_type.family.calculator_key == "water_meter":
            raise ToolFailure("Счётчики воды считает встроенный калькулятор — шаблон поверки им не нужен")
        from apps.verification.models import VerificationTemplate

        other = args.get("template_id")
        if other and other != job.verification_template_id:
            # Готовый шаблон (часто — общий на семейство и уже проверенный) подходит и этому типу:
            # его не переписываем и не снимаем с проверки, только добавляем наборы эталонов.
            template = VerificationTemplate.objects.filter(pk=other, is_active=True).first()
            if template is None or template.family_id != job.si_type.family_id:
                raise ToolFailure(f"Шаблона поверки {other} нет или он другого семейства")
            if args.get("plan"):
                raise ToolFailure("Чужой шаблон здесь не переписывается: передай template_id и kits (без plan), "
                                  "чтобы использовать его как есть, или сделай свой черновик без template_id")
            new_kits = _kits(args.get("kits"))
            template.kits.add(*new_kits)
            job.verification_template = template
            job.saved_at = timezone.now()
            job.save(update_fields=["verification_template", "saved_at"])
            return ToolResult(f"Тип будет поверяться по готовому шаблону «{template.title}» (id {template.pk}); "
                              f"добавлены наборы эталонов: {', '.join(map(str, new_kits)) or 'нет'}.")
        if not args.get("plan"):
            raise ToolFailure("plan — шаблон поверки (или template_id готового шаблона, чтобы взять его как есть)")
        template_id = job.verification_template_id
        data = {"title": args.get("title"), "family": job.si_type.family_id,
                "si_type": None if args.get("for_family") else job.si_type.pk,
                "plan": args.get("plan"), "note": args.get("note") or ""}
        if "kits" in args:
            data["kits"] = args.get("kits") or []
        try:
            report = templates.check_example(templates.clean_template(args.get("plan")), args["example"]) \
                if args.get("example") else None
            template = templates.save_template(ctx.user, data, sources=sources, plan_check=report,
                                               template_id=template_id, via_assistant=True, force_review=True)
        except templates.TemplateError as exc:
            raise ToolFailure(f"шаблон: {exc}") from exc
        job.verification_template = template
        job.saved_at = timezone.now()
        job.save(update_fields=["verification_template", "saved_at"])
        kits = ", ".join(str(k) for k in template.kits.all()) or "не выбраны"
        return ToolResult(f"Черновик шаблона поверки сохранён (id {template.pk}, «{template.title}»), наборы эталонов: "
                          f"{kits}. Ждёт проверки метролога.")
    if kind == "attestation":
        card = {k: args.get(k) for k in ("title", "equipment_kind", "plan", "note")}
        try:
            program = service.save_program(ctx.user, card, sources=sources,
                                           program_id=args.get("program_id") or job.attestation_program_id,
                                           via_assistant=True, force_review=True)
        except service.AttestationError as exc:
            raise ToolFailure(f"шаблон аттестации: {exc}") from exc
        job.attestation_program = program
        job.saved_at = timezone.now()
        job.save(update_fields=["attestation_program", "saved_at"])
        return ToolResult(f"Черновик шаблона аттестации сохранён (id {program.pk}, «{program.title}»). "
                          "Ждёт проверки метролога.")
    raise ToolFailure("kind — verification | attestation")


HOST_ONLY = [
    Tool("ei_template_job_save", "Сохраняю черновик шаблона",
         "ТОЛЬКО для задач «ФОНОВАЯ РАБОТА EI_doc» (job_id из задачи): сохранить черновик шаблона для нового типа "
         "СИ — без карточки, метролог проверит его на вкладке «Типы СИ». kind=verification: title, plan (формат — "
         "как в ei_verification_template_propose), kits — id строк перечня эталонов (ei_standard_kits), note, "
         "example (сверка), for_family=true — шаблон на всё семейство, а не на тип; template_id готового шаблона "
         "БЕЗ plan — взять его для этого типа как есть (добавятся только kits). "
         "kind=attestation (только испытательное оборудование): title, equipment_kind, plan (как в "
         "ei_attestation_template_propose), note, program_id. sources — обязательно. Повторный вызов обновляет "
         "тот же черновик.",
         _obj({"job_id": {"type": "integer"}, "kind": {"type": "string", "enum": ["verification", "attestation"]},
               "title": STR, "plan": {"type": "object"}, "kits": {"type": "array", "items": {"type": "integer"}},
               "note": STR, "example": {"type": "object"}, "for_family": {"type": "boolean"},
               "template_id": {"type": "integer"}, "equipment_kind": STR, "program_id": {"type": "integer"},
               "sources": {"type": "array", "items": STR}}, ["job_id", "kind", "sources"]),
         template_job_save),
    Tool("ei_company_memory_propose", "Запоминаю для всей компании",
         "Записать правило или факт в ОБЩУЮ память компании (видят все сотрудники и ассистент у каждого). "
         "Человек подтверждает карточкой; менять общую память может метролог или руководитель. Личное — "
         "в свою память (memory_save), не сюда.",
         _obj({"text": STR}, ["text"]), company_memory_save),
    Tool("ei_company_memory_delete", "Удаляю из общей памяти",
         "Удалить запись общей памяти компании по id (id — в разделе «Общая память компании» инструкций).",
         _obj({"memory_id": {"type": "integer"}}, ["memory_id"]), company_memory_delete),
]


def registry(user) -> dict[str, Tool]:
    tools = {}
    for t in [*builtin_tools(), *HOST_ONLY]:
        if t.name in SKIP or (t.name == "web_search" and not web.search_configured()):
            continue
        tools[RENAME.get(t.name, t.name)] = t
    return tools


def is_change(name: str) -> bool:
    """Меняет данные компании → карточка подтверждения у агента. Остальное — чтение."""
    return name.endswith("_propose") or name == "ei_company_memory_delete"


def tool_spec(name: str, tool: Tool) -> dict:
    spec = {"name": name, "title": tool.label, "description": tool.description, "inputSchema": tool.parameters}
    if is_change(name):
        spec["annotations"] = {"readOnlyHint": False, "destructiveHint": False}
        spec["_meta"] = {PREVIEW_META: True}
    else:
        # Файлы и черновики (file_create, protocol_draft) данных компании не меняют — без карточки.
        spec["annotations"] = {"readOnlyHint": True}
    return spec


# ── инструкции: то, что раньше было в «динамической» части системного промпта ──

def instructions(user) -> str:
    employee = getattr(user, "employee", None)
    who = employee.full_name if employee else user.email
    position = f", {employee.position}" if employee and employee.position else ""
    now = timezone.localtime()
    # Час, а не минуты: инструкции — часть промпта, и каждая новая минута сбрасывала бы кэш модели.
    parts = [
        f"## Сейчас\n{now:%d.%m.%Y}, {prompt.WEEKDAYS[now.weekday()]}, около {now:%H}:00 (Киров). "
        f"Пользователь: {who}{position}, роль в EI_doc — {user.get_role_display()}."
    ]
    rules = prompt.AssistantSettings.current().extra_instructions.strip()
    if rules:
        parts.append("## Правила компании (от руководства — обязательны)\n" + rules)
    company = [f"- [{m.pk}] {m.text}" for m in MemoryItem.objects.filter(scope=Scope.COMPANY).order_by("pk")]
    if company:
        parts.append("## Общая память компании (id — для ei_company_memory_delete)\n" + "\n".join(company))
    always, toc = prompt._files_toc(user)
    if always:
        parts.append("## Файлы компании, всегда в контексте\n" + always)
    if toc:
        parts.append("## Файлы компании, доступные поиском (context_search / context_read по id)\n" + toc)
    return "\n\n".join(parts)


# ── выполнение ──────────────────────────────────────────────────────────

def _text(content: str, *, error: bool = False, **extra) -> dict:
    return {"content": [{"type": "text", "text": content}], "isError": error, **extra}


def _short(value) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= 400 else s[:400] + "…"


def card_rows(action: PendingAction) -> list[list[str]]:
    rows = [["Действие", action.title]]
    payload = action.payload or {}
    if payload.get("view"):
        rows += [[str(r[0]), str(r[1])] for r in payload["view"] if isinstance(r, list | tuple) and len(r) >= 2]
    else:
        for key, value in payload.items():
            if value in (None, "", [], {}):
                continue
            label = LABELS.get(key, key)
            if key in BULKY:
                rows.append([label, f"есть ({len(json.dumps(value, ensure_ascii=False, default=str))} симв.)"])
            else:
                rows.append([label, _short(value)])
    if action.sources:
        rows.append(["Источники", _short("; ".join(map(str, action.sources)))])
    return rows


def _action(result: ToolResult) -> PendingAction | None:
    pk = (result.ui or {}).get("action")
    return PendingAction.objects.filter(pk=pk).first() if pk else None


def _files(result: ToolResult, user) -> list[dict]:
    out = []
    for f in (result.ui or {}).get("files") or []:
        att = Attachment.objects.filter(pk=f.get("id"), user=user).first()
        if att is None or att.size > MAX_FILE_BYTES:
            continue
        with att.file.open("rb") as fh:
            data = fh.read()
        out.append({"type": "resource", "resource": {
            "uri": f"ei-doc://attachments/{att.pk}", "name": att.name, "mimeType": att.mime or "application/octet-stream",
            "blob": base64.b64encode(data).decode()}})
    return out


class _Rollback(Exception):
    pass


def call(user, conversation: Conversation, name: str, args: dict, *, dry_run: bool = False) -> dict:
    tool = registry(user).get(name)
    if tool is None:
        return _text(f"Нет инструмента {name}", error=True)
    ctx = ToolContext(user=user, conversation=conversation)
    try:
        if is_change(name) and dry_run:
            # Обработчик заводит PendingAction; нам нужны только его строки, поэтому — откат.
            preview: dict = {}
            try:
                with transaction.atomic():
                    result = tool.handler(ctx, args)
                    action = _action(result)
                    preview = {"view": card_rows(action)} if action else {"text": result.content}
                    raise _Rollback
            except _Rollback:
                pass
            if "view" not in preview:  # подтверждать нечего: «такой тип уже есть» и т. п.
                return _text(preview["text"])
            return _text("Карточка подготовлена", structuredContent=preview)
        result = tool.handler(ctx, args)
        if is_change(name):
            action = _action(result)
            if action is None:
                return _text(result.content, error=result.is_error)
            action = actions.decide(action, user, confirm=True)
            if action.status == PendingAction.Status.DONE:
                _, done_text = actions._registry()
                return _text(f"Выполнено: {done_text[action.kind](action.result)}",
                             _meta={"agentkit/refresh": action.kind.startswith("memory_company")})
            return _text(f"Не выполнено «{action.title}»: {action.error}", error=True)
        content = [{"type": "text", "text": result.content}, *_files(result, user)]
        return {"content": content, "isError": result.is_error}
    except ToolFailure as exc:
        return _text(f"Ошибка: {exc}", error=True)
    except (APIException, actions.ActionError) as exc:
        return _text(f"Ошибка: {getattr(exc, 'detail', exc)}", error=True)
    except Exception as exc:  # noqa: BLE001 — модель увидит причину и поправит аргументы
        log.exception("agent host tool %s failed", name)
        return _text(f"Ошибка: {exc}", error=True)


# ── HTTP: Streamable HTTP без потоков — один JSON-ответ на запрос ────────

def _authorized(request) -> bool:
    expected = getattr(settings, "AGENT_HOST_TOKEN", "")
    got = request.headers.get("Authorization", "")
    # Байты, а не строки: compare_digest падает на не-ASCII, а заголовок может прислать кто угодно.
    return bool(expected) and hmac.compare_digest(got.encode(), f"Bearer {expected}".encode())


def _user(request):
    raw = unquote(request.headers.get("X-Agent-User-Id", ""))
    if not raw.isdigit():
        return None
    return get_user_model().objects.filter(pk=int(raw), is_active=True).first()


def _conversation(user, request) -> Conversation:
    sid = unquote(request.headers.get("X-Agent-Session-Id", "")) or "none"
    conv, _ = Conversation.objects.get_or_create(user=user, external_id=f"agentkit:{sid}"[:120],
                                                 defaults={"title": "Разговор с агентом"})
    return conv


def _rpc(msg_id, result=None, error: str | None = None, code: int = -32602):
    body = {"jsonrpc": "2.0", "id": msg_id}
    body["error" if error else "result"] = {"code": code, "message": error} if error else result
    return JsonResponse(body, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
def mcp_endpoint(request):
    if request.method != "POST":
        return HttpResponse(status=405)  # потоков от сервера (GET) нет
    if not _authorized(request):
        return HttpResponse("нужен токен агента: Authorization: Bearer <AGENT_HOST_TOKEN>", status=401)
    try:
        msg = json.loads(request.body or b"{}")
    except ValueError:
        return _rpc(None, error="не JSON", code=-32700)
    if not isinstance(msg, dict):
        return _rpc(None, error="пакеты запросов не поддерживаются", code=-32600)
    if "id" not in msg:
        return HttpResponse(status=202)  # уведомления (notifications/initialized)
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "ping":
        return _rpc(msg["id"], {})
    user = _user(request)
    if user is None:
        return _rpc(msg["id"], error="пользователь EI_doc не найден или отключён (X-Agent-User-Id)")
    if method == "initialize":
        return _rpc(msg["id"], {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ei-doc", "version": "1"},
            "instructions": instructions(user),
        })
    if method == "tools/list":
        return _rpc(msg["id"], {"tools": [tool_spec(n, t) for n, t in registry(user).items()]})
    if method == "tools/call":
        dry = bool((params.get("_meta") or {}).get(DRY_RUN_META))
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _rpc(msg["id"], error="arguments — не объект")
        return _rpc(msg["id"], call(user, _conversation(user, request), str(params.get("name")), args, dry_run=dry))
    return _rpc(msg["id"], error=f"метод {method} не поддерживается", code=-32601)
