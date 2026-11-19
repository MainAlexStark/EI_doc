"""Режим «Создать шаблон протокола поверки» — отдельный чат с агентом на каждый тип СИ.

Зачем. Протоколы поверки оформлены примерно одинаково (шапка, средства поверки, условия,
заключение — как у счётчиков воды), а наполнение у каждого типа своё и нелинейное: операции,
поверяемые точки, величины в точке, формулы и допуски задаёт методика поверки. Поэтому шаблон
для каждого типа собирает агент по методике, а человек сразу видит протокол и правит его словами.

Поток:

1. Человек ищет тип в Госреестре (ФИФ) и выбирает его → ``ensure_si_type`` заводит тип в
   справочнике, если его ещё нет (вид СИ — угадывается по наименованию типа, иначе «Прочие СИ»),
   документы типа (методика, описание типа) регистрируются и скачиваются;
2. ``open_workspace`` заводит рабочее место: свой чат (``Conversation``, в общем списке чатов
   не показывается) и сразу первое задание агенту — прочитать методику и все документы типа и
   сохранить первый черновик;
3. агент в этом чате работает со своими инструментами (``tools_for``): читает текущий черновик
   (``ei_workspace_template_get``) и сохраняет его целиком (``ei_workspace_template_save``) —
   без карточки «Подтвердить»: черновик выключен (``is_active=False``), поверки по нему не идут,
   а человек и так видит каждое сохранение в окне протокола рядом с чатом (``revision``);
4. «Продолжить» (стрелка вправо вверху) → ``publish``: черновик становится действующим шаблоном
   типа, прежний шаблон этого типа выключается. Дальше на экране «Приёмки» по этому типу идут
   протоколы по шаблону. Шаблон от поверителя по-прежнему ждёт метролога (``needs_review``):
   протокол по нему не подпишется, пока метролог не проверит.

После публикации правки в том же чате идут в новый черновик; действующий шаблон меняется
только следующей «Продолжить» — поверка, начатая утром, не поедет от правки днём.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.assistant.models import Conversation, Message, TemplateWorkspace
from apps.assistant.tools import STR, Tool, ToolContext, ToolFailure, ToolResult, _dump, _kits, _obj

log = logging.getLogger(__name__)

WATER_CALCULATOR = "water_meter"


class WorkspaceError(ValueError):
    """Ошибка для человека (экран) — текстом."""


# ---------------------------------------------------------------------------
# Тип СИ из Госреестра
# ---------------------------------------------------------------------------
def ensure_si_type(user, *, si_type_id=None, fif_ident: str = "", name: str = ""):
    """Тип СИ для рабочего места: уже заведённый или новый — из карточки ФИФ.

    Новый тип заводится так же, как на экране «Типы СИ» (поверитель — ``needs_review``), только без
    фоновой работы ``TemplateJob``: шаблон будет делаться здесь, в чате.
    """
    from rest_framework.exceptions import PermissionDenied

    from apps.arshin import documents as fif_documents
    from apps.arshin.fif import FifClient, FifUnavailable
    from apps.catalog.api_fif import schedule_documents
    from apps.catalog.api_types import can_create, default_family, is_reviewer
    from apps.catalog.models import SiType
    from apps.catalog.standard_kits import guess_family

    if si_type_id:
        si_type = SiType.objects.select_related("family").filter(pk=si_type_id, is_active=True).first()
        if si_type is None:
            raise WorkspaceError(f"Типа СИ {si_type_id} нет в справочнике")
        return si_type
    fif_ident = str(fif_ident or "").strip()
    if not fif_ident:
        raise WorkspaceError("Выберите тип из Госреестра")
    if not can_create(user):
        raise PermissionDenied("Заводить типы СИ может поверитель, метролог или руководитель")
    try:
        card = FifClient().get_type(fif_ident)
    except FifUnavailable as exc:
        raise WorkspaceError(f"Госреестр (ФИФ) сейчас не отвечает: {exc}") from exc
    fif_documents.register_documents(card)
    name = " ".join(str(name or "").split())[:200] or (card.variants[0] if card.variants else "") \
        or card.notation or card.title
    known = SiType.objects.select_related("family").filter(is_active=True, fif_uuid=card.uuid)
    existing = known.filter(name=name).first() or SiType.objects.select_related("family").filter(
        registry_number=card.registry_number, name=name).first() or known.order_by("name").first()
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
            existing.save(update_fields=["is_active"])
        return existing
    family = guess_family(f"{card.title} {' '.join(card.variants[:3])}") or default_family()
    reviewer = is_reviewer(user)
    months = next((row.get("months") for row in card.mpi if row.get("months")), None)
    fields = dict(
        family=family, registry_number=card.registry_number, name=name, manufacturer=card.manufacturer,
        fif_uuid=card.uuid, type_title=card.title[:255], certificate_valid_to=card.valid_to, fif_raw=card.raw or {},
        created_by=user, needs_review=not reviewer, reviewed_by=user if reviewer else None,
        reviewed_at=timezone.now() if reviewer else None,
    )
    if months:
        fields["verification_interval_months"] = int(months)
    try:
        with transaction.atomic():
            si_type = SiType.objects.create(**fields)
    except IntegrityError:
        si_type = SiType.objects.select_related("family").get(registry_number=card.registry_number, name=name)
    transaction.on_commit(lambda: schedule_documents(card.uuid))
    return si_type


# ---------------------------------------------------------------------------
# Рабочее место
# ---------------------------------------------------------------------------
def _builtin_model() -> str:
    from apps.assistant.template_jobs import builtin_model

    return builtin_model()


def open_workspace(user, si_type) -> tuple[TemplateWorkspace, bool]:
    """Рабочее место сотрудника по типу: есть — вернуть, нет — завести и сразу дать агенту задание."""
    from apps.verification.models import VerificationTemplate

    workspace = TemplateWorkspace.objects.select_related("conversation", "si_type__family").filter(
        si_type=si_type, user=user).first()
    if workspace is not None:
        return workspace, False
    model = _builtin_model()
    if not model:
        raise WorkspaceError(
            "Ассистент не подключён: в .env EI_doc нет ключа модели (OPENROUTER_API_KEY или YANDEX_VISION_*). "
            "Режим шаблонов работает со встроенным ассистентом EI_doc."
        )
    from apps.assistant import agent

    with transaction.atomic():
        conversation = Conversation.objects.create(
            user=user, model=model, title=agent.title_from(f"Шаблон протокола: {si_type.name}"))
        workspace = TemplateWorkspace.objects.create(si_type=si_type, user=user, conversation=conversation)
        # Уже действует свой шаблон типа (сделан раньше — фоновой работой или в другом чате) — от него и
        # отталкиваемся: агент правит его копию-черновик, а не начинает с нуля.
        current = VerificationTemplate.objects.filter(si_type=si_type, is_active=True).order_by("-updated_at").first()
        if current is not None:
            workspace.published = current
            workspace.title = current.title
            workspace.status = TemplateWorkspace.Status.PUBLISHED
            workspace.published_at = current.updated_at
            workspace.save(update_fields=["published", "title", "status", "published_at"])
        Message.objects.create(conversation=conversation, role=Message.Role.USER, content=first_task(workspace))
        agent.start_run(conversation, model)
    return workspace, True


def first_task(workspace: TemplateWorkspace) -> str:
    t = workspace.si_type
    if workspace.published_id:
        return (f"Для типа «{t.name}» (№ {t.registry_number}) уже есть шаблон протокола поверки. Сверь его с методикой "
                "поверки и документами типа из ФИФ, сохрани черновик (если всё верно — без изменений) и коротко "
                "скажи, что в нём и что стоит проверить.")
    return (f"Сделай шаблон протокола поверки для типа «{t.name}» (№ {t.registry_number} в Госреестре"
            f"{', ' + t.manufacturer if t.manufacturer else ''}). Прочитай методику поверки и все документы типа "
            "из ФИФ, подбери наборы эталонов из перечня компании, сохрани первый черновик и коротко опиши, что "
            "вошло и что мне проверить.")


def for_conversation(conversation) -> TemplateWorkspace | None:
    if conversation is None or not getattr(conversation, "pk", None):
        return None
    return TemplateWorkspace.objects.select_related("si_type__family", "draft", "published").filter(
        conversation_id=conversation.pk).first()


def current_template(workspace: TemplateWorkspace):
    """Что показывать в окне протокола: черновик, а если его нет — действующий шаблон."""
    return workspace.draft or workspace.published


def _documents(si_type) -> list:
    from apps.arshin.models import FifDocument

    if not si_type.fif_uuid:
        return []
    return list(FifDocument.objects.filter(mit_uuid=si_type.fif_uuid).order_by("kind", "pk"))


def change_family(workspace: TemplateWorkspace, user, family_id) -> TemplateWorkspace:
    """Вид СИ угадался неверно (или тип раньше завели счётчиком воды) — поправить прямо здесь."""
    from rest_framework.exceptions import PermissionDenied

    from apps.catalog.api_types import is_reviewer
    from apps.catalog.models import MeasurementFamily

    family = MeasurementFamily.objects.filter(pk=family_id).first()
    if family is None:
        raise WorkspaceError("Нет такого вида СИ")
    si_type = workspace.si_type
    if not (si_type.needs_review or is_reviewer(user)):
        raise PermissionDenied("Тип уже проверен метрологом — вид СИ меняет метролог или руководитель")
    with transaction.atomic():
        si_type.family = family
        si_type.save(update_fields=["family"])
        if workspace.draft_id:
            workspace.draft.family = family
            workspace.draft.save(update_fields=["family", "updated_at"])
        workspace.revision += 1
        workspace.save(update_fields=["revision", "updated_at"])
    return workspace


# ---------------------------------------------------------------------------
# Черновик и публикация
# ---------------------------------------------------------------------------
def _check_not_water(si_type) -> None:
    if si_type.family.calculator_key == WATER_CALCULATOR:
        raise ToolFailure(
            f"Тип «{si_type}» стоит в семействе «{si_type.family.name}» — их поверяет встроенный калькулятор EI_doc, "
            "шаблон ему не нужен. Если это НЕ счётчик воды (вид СИ угадался неверно), скажи человеку поменять "
            "«Вид СИ» в шапке рабочего места — и продолжай."
        )


def _free_title(title: str, *, own: set[int]) -> str:
    """Название не должно совпасть с чужим шаблоном; с действующим шаблоном этого же места — можно (временно)."""
    from apps.verification.models import VerificationTemplate

    clash = VerificationTemplate.objects.filter(title=title).exclude(pk__in=own)
    if clash.exists():
        raise ToolFailure(f"Шаблон «{title}» уже есть у другого типа — назови иначе (например, с номером в Госреестре)")
    return title


def save_draft(workspace: TemplateWorkspace, user, args: dict) -> tuple[object, dict | None]:
    """Сохранить черновик целиком. Действующий шаблон не трогается."""
    from apps.verification import templates
    from apps.verification.models import VerificationTemplate

    si_type = workspace.si_type
    _check_not_water(si_type)
    forbidden = {"measurements", "readings", "results", "instrument", "serial_number"} & set(args)
    if forbidden:
        raise ToolFailure(f"Шаблон — только структура, без {', '.join(sorted(forbidden))}: прибор и показания "
                          "вносит поверитель на поверке")
    title = " ".join(str(args.get("title") or workspace.title or "").split())[:250]
    if not title:
        raise ToolFailure("title — название шаблона: вид СИ, тип и методика («Манометры ДМ 02 — МИ 2124-90»)")
    sources = [str(x) for x in (args.get("sources") or []) if str(x).strip()]
    if not sources:
        raise ToolFailure("sources — откуда взяты операции, точки, формулы и допуски: методика (пункты), описание "
                          "типа, ГОСТ")
    try:
        plan = templates.clean_template(args.get("plan"))
        report = templates.check_example(plan, args["example"]) if args.get("example") else None
    except templates.TemplateError as exc:
        raise ToolFailure(f"шаблон: {exc}") from exc
    kits = _kits(args.get("kits")) if "kits" in args else None

    with transaction.atomic():
        workspace = TemplateWorkspace.objects.select_for_update().get(pk=workspace.pk)
        draft = workspace.draft
        own = {pk for pk in (workspace.draft_id, workspace.published_id) if pk}
        if workspace.published_id and title == workspace.published.title:
            # Новая редакция действующего шаблона: своё имя ей дадим при «Продолжить», пока — с пометкой.
            stored = _free_title(f"{title} — новая редакция", own=own)
        else:
            stored = _free_title(title, own=own)
        if draft is None:
            draft = VerificationTemplate(created_by=user, is_active=False)
            if kits is None and workspace.published_id:
                kits = list(workspace.published.kits.all())
        draft.title, draft.family, draft.si_type, draft.plan = stored, si_type.family, si_type, plan
        draft.note = str(args.get("note") or "")[:2000]
        draft.sources = sources
        draft.plan_check = report or {}
        draft.created_via_assistant = True
        draft.needs_review = True
        draft.reviewed_by, draft.reviewed_at = None, None
        draft.is_active = False
        try:
            draft.save()
        except IntegrityError as exc:
            raise ToolFailure(f"Шаблон «{stored}» уже есть — назови иначе") from exc
        if kits is not None:
            draft.kits.set(kits)
        workspace.draft = draft
        workspace.title = title
        workspace.status = TemplateWorkspace.Status.DRAFT
        workspace.revision += 1
        workspace.save(update_fields=["draft", "title", "status", "revision", "updated_at"])
    return draft, report


def publish(workspace: TemplateWorkspace, user):
    """«Продолжить»: черновик → действующий шаблон типа. Прежний шаблон типа выключается."""
    from rest_framework.exceptions import PermissionDenied

    from apps.catalog.api_types import can_create, is_reviewer
    from apps.verification import templates
    from apps.verification.models import VerificationTemplate

    if not can_create(user):
        raise PermissionDenied("Наблюдатель не публикует шаблоны")
    with transaction.atomic():
        workspace = TemplateWorkspace.objects.select_for_update().select_related(
            "si_type__family", "draft", "published").get(pk=workspace.pk)
        draft = workspace.draft
        if draft is None:
            if workspace.published_id:
                return workspace  # нечего публиковать — действует то, что есть
            raise WorkspaceError("Черновика ещё нет — дождитесь, пока ассистент его сохранит")
        if workspace.si_type.family.calculator_key == WATER_CALCULATOR:
            raise WorkspaceError("Тип стоит в «Счётчиках воды» — их считает калькулятор. Поменяйте «Вид СИ», если это не счётчик воды")
        try:
            draft.plan = templates.clean_template(draft.plan)
        except templates.TemplateError as exc:
            raise WorkspaceError(f"Черновик не проходит проверку: {exc}") from exc
        now = timezone.now()
        # Всё остальное действующее «своё» у типа выключаем: template_for взял бы самое свежее, но так понятнее.
        old = list(VerificationTemplate.objects.filter(si_type=workspace.si_type, is_active=True).exclude(pk=draft.pk))
        for template in old:
            template.is_active = False
            if template.title == workspace.title:
                template.title = f"{template.title} (ред. до {timezone.localtime(now):%d.%m.%Y}, №{template.pk})"[:250]
            template.save(update_fields=["is_active", "title", "updated_at"])
        reviewer = is_reviewer(user)
        draft.title = workspace.title or draft.title
        draft.is_active = True
        draft.needs_review = not reviewer
        draft.reviewed_by = user if reviewer else None
        draft.reviewed_at = now if reviewer else None
        draft.save()
        workspace.published, workspace.draft = draft, None
        workspace.status = TemplateWorkspace.Status.PUBLISHED
        workspace.published_at = now
        workspace.revision += 1
        workspace.save(update_fields=["published", "draft", "status", "published_at", "revision", "updated_at"])
        Message.objects.create(
            conversation=workspace.conversation, role=Message.Role.EVENT,
            content=f"[Система] Человек нажал «Продолжить»: шаблон «{draft.title}» (id {draft.pk}) теперь действует "
                    "для этого типа — по нему создаются протоколы поверки. Дальнейшие правки сохраняй как обычно: "
                    "они пойдут в новый черновик и вступят в силу после следующего «Продолжить».",
        )
    return workspace


def preview(workspace: TemplateWorkspace, fmt: str) -> bytes:
    from apps.verification import templates

    template = current_template(workspace)
    if template is None:
        raise WorkspaceError("Черновика ещё нет")
    spec = templates.preview_spec(template.plan, title=workspace.title or template.title)
    return templates.render(spec, fmt)


# ---------------------------------------------------------------------------
# Для экрана
# ---------------------------------------------------------------------------
def _digest(template) -> dict | None:
    if template is None:
        return None
    plan = template.plan or {}
    return {
        "id": template.pk, "title": template.title, "is_active": template.is_active,
        "needs_review": template.needs_review, "updated_at": template.updated_at,
        "method": plan.get("method", ""), "si_kind": plan.get("si_kind", ""), "unit": plan.get("unit", ""),
        "params": [{"label": p.get("label"), "unit": p.get("unit", ""), "from_type": bool(p.get("from_type"))}
                   for p in plan.get("params") or []],
        "operations": [op.get("label") for op in plan.get("operations") or []],
        "modes": [str(m.get("label") or m.get("setpoint")) for m in plan.get("modes") or []],
        "points": [str(p.get("label") or p.get("key")) for p in plan.get("points") or []],
        "repeats": plan.get("repeats"),
        "characteristics": [str(c.get("label") or c.get("key")) for c in plan.get("characteristics") or []],
        "kits": [str(k) for k in template.kits.all()],
        "sources": template.sources or [],
        "check": None if not template.plan_check else {"ok": template.plan_check.get("ok")},
    }


def workspace_json(workspace: TemplateWorkspace, *, full: bool = True) -> dict:
    t = workspace.si_type
    run = workspace.conversation.runs.order_by("-pk").first()
    body = {
        "id": workspace.pk, "conversation": workspace.conversation_id, "title": workspace.title,
        "status": workspace.status, "status_display": workspace.get_status_display(),
        "revision": workspace.revision, "published_at": workspace.published_at, "updated_at": workspace.updated_at,
        "has_draft": workspace.draft_id is not None,
        "si_type": {"id": t.pk, "name": t.name, "registry_number": t.registry_number, "manufacturer": t.manufacturer,
                    "type_title": t.type_title, "family": t.family_id, "family_name": t.family.name,
                    "is_water": t.family.calculator_key == WATER_CALCULATOR, "needs_review": t.needs_review},
        "agent_busy": bool(run and run.is_active),
        "agent_activity": run.activity if run and run.is_active else "",
    }
    if full:
        body["draft"] = _digest(workspace.draft)
        body["published"] = _digest(workspace.published)
        body["documents"] = [{"id": d.pk, "kind": d.kind, "kind_display": d.get_kind_display(), "title": d.title,
                              "downloaded": bool(d.file), "file_url": f"/api/catalog/fif/documents/{d.pk}/file/"}
                             for d in _documents(t)]
    return body


# ---------------------------------------------------------------------------
# Агент: инструменты и раздел промта (только в чате рабочего места)
# ---------------------------------------------------------------------------
def prompt_for(conversation) -> str:
    workspace = for_conversation(conversation)
    if workspace is None:
        return ""
    t = workspace.si_type
    docs = _documents(t)
    lines = [
        "# Режим «Создать шаблон протокола поверки» (этот чат)",
        "Этот чат — рабочее место шаблона протокола поверки ОДНОГО типа СИ. Рядом с чатом человек видит протокол по "
        "твоему черновику: он перерисовывается после каждого ei_workspace_template_save. Человек правит шаблон "
        "словами («добавь проверку герметичности», «точки через 20 %»), ты вносишь правку и сохраняешь черновик "
        "ЦЕЛИКОМ (ei_workspace_template_get → правка → ei_workspace_template_save). Карточек «Подтвердить» здесь "
        "нет и *_propose для шаблона поверки не нужны: черновик выключен, пока человек не нажмёт «Продолжить» "
        "(стрелка вправо вверху) — тогда шаблон начнёт действовать для этого типа.",
        "",
        "Тип СИ:",
        f"- id в EI_doc {t.pk}; «{t.name}»; № в Госреестре {t.registry_number}; изготовитель {t.manufacturer or '—'}",
        f"- наименование типа в ФИФ: {t.type_title or '—'}; mit_uuid {t.fif_uuid or '—'}",
        f"- вид СИ в EI_doc: {t.family.name} (id {t.family_id}); характеристики типа (limits): {t.limits or '—'}",
    ]
    if docs:
        lines.append("- документы типа из ФИФ (читай ei_fif_document_read document_id=… si_type=" + str(t.pk)
                     + ", длинные — по частям offset): "
                     + "; ".join(f"[{d.pk}] {d.get_kind_display()}: «{d.title}»" for d in docs))
    else:
        lines.append(f"- документы типа ещё не получены: ei_fif_document_read si_type={t.pk} (kind=meth, затем spec)")
    if t.family.calculator_key == WATER_CALCULATOR:
        lines.append("- ВНИМАНИЕ: тип стоит в «Счётчиках воды» (калькулятор). Если по документам это не счётчик воды — "
                     "попроси человека поменять «Вид СИ» в шапке рабочего места.")
    lines += [
        "",
        "Как работать:",
        "1. Прочитай ВСЕ документы типа: методику поверки целиком (операции, средства, условия, поверяемые точки, "
        "обработка результатов, допуски), описание типа (диапазоны, классы, модификации) и прочие. Методики нет "
        "в ФИФ — get_type во ФГИС, web_search/web_fetch по обозначению.",
        "2. Перечень эталонов компании: ei_standard_kits — подходящие строки в kits. Нет подходящих — скажи, что "
        "метрологу нужно добавить набор.",
        "3. Сохрани черновик ei_workspace_template_save; если в методике есть пример расчёта — передай его в example "
        "(сверка формул). Каждая операция, точка, формула и допуск — с пунктом документа в source.",
        "4. Ответ человеку — коротко: что вошло, откуда, допущения, что проверить. Протокол он видит сам — не "
        "пересказывай шаблон целиком.",
        "Параметры, которые различаются у модификаций (верхний предел, класс), — params без from_type: их внесёт "
        "поверитель, протокол посчитает точки сам. Не выдумывай значений, которых нет в документах.",
    ]
    if workspace.published_id:
        lines.append(f"Действующий шаблон этого типа: «{workspace.published.title}» (id {workspace.published_id}).")
    if workspace.draft_id:
        lines.append(f"Черновик: id {workspace.draft_id}, редакция {workspace.revision}.")
    return "\n".join(lines)


def _workspace(ctx: ToolContext) -> TemplateWorkspace:
    workspace = for_conversation(ctx.conversation)
    if workspace is None:
        raise ToolFailure("Этот инструмент работает только в режиме «Создать шаблон протокола поверки»")
    if workspace.user_id != ctx.user.pk:
        raise ToolFailure("Рабочее место другого сотрудника")
    return workspace


def tool_get(ctx: ToolContext, args: dict) -> ToolResult:
    workspace = _workspace(ctx)
    template = current_template(workspace)
    if template is None:
        return ToolResult("Черновика пока нет — составь шаблон по методике и сохрани ei_workspace_template_save.")
    which = "черновик" if template.pk == workspace.draft_id else "действующий шаблон (черновика нет)"
    return ToolResult(_dump({"which": which, "title": workspace.title or template.title, "plan": template.plan,
                             "kits": [k.pk for k in template.kits.all()], "note": template.note,
                             "sources": template.sources}))


def tool_save(ctx: ToolContext, args: dict) -> ToolResult:
    from apps.verification import templates

    workspace = _workspace(ctx)
    draft, report = save_draft(workspace, ctx.user, args)
    revision = TemplateWorkspace.objects.values_list("revision", flat=True).get(pk=workspace.pk)
    plan = draft.plan
    lines = [
        f"Черновик сохранён (редакция {revision}), протокол рядом с чатом обновился.",
        f"Операции: {'; '.join(op['label'] for op in plan['operations'])}.",
        f"Поверяемые точки: {'; '.join(str(m.get('label') or m['setpoint']) for m in plan['modes'])}.",
        f"Наборы эталонов: {', '.join(str(k) for k in draft.kits.all()) or 'не выбраны'}.",
    ]
    if report is not None:
        lines.append("Сверка с примером: " + ("сходится." if report["ok"] else "НЕ СХОДИТСЯ — поправь формулы: "
                     + "; ".join(f"{r['mode']}/{r['key']}: {r['expected']} ≠ {r['computed']}"
                                 for r in report["rows"] if not r["ok"])))
    try:
        templates.preview_spec(plan, title=draft.title)
    except Exception as exc:  # noqa: BLE001 — шаблон сохранён, но протокол не собирается: пусть агент поправит
        lines.append(f"ВНИМАНИЕ: протокол по черновику не собирается: {exc}")
    lines.append("Шаблон начнёт действовать, когда человек нажмёт «Продолжить» вверху.")
    return ToolResult("\n".join(lines), {"summary": f"Черновик шаблона — редакция {revision}"})


def tools_for(conversation) -> list[Tool]:
    if for_conversation(conversation) is None:
        return []
    from apps.verification.templates import template_help

    return [
        Tool("ei_workspace_template_get", "Смотрю черновик шаблона",
             "Текущий черновик шаблона протокола поверки этого рабочего места целиком (plan, kits, note, sources); "
             "черновика нет — действующий шаблон типа. Перед правкой — всегда прочитай.",
             _obj({}), tool_get),
        Tool("ei_workspace_template_save", "Сохраняю черновик шаблона",
             "Сохранить черновик шаблона протокола поверки этого типа ЦЕЛИКОМ (без карточки — человек видит "
             "протокол рядом с чатом). title — название; plan — шаблон; kits — id строк перечня эталонов "
             "(ei_standard_kits); note; sources — обязательно (методика с пунктами, описание типа); example — пример "
             "из методики для сверки формул ({params, modes, source}). Формат plan: " + template_help(),
             _obj({"title": STR, "plan": {"type": "object"}, "kits": {"type": "array", "items": {"type": "integer"}},
                   "note": STR, "example": {"type": "object"}, "sources": {"type": "array", "items": STR}},
                  ["plan", "sources"]),
             tool_save),
    ]
