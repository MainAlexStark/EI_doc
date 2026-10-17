"""API ассистента: чаты, прогоны, файлы, контекст, память, MCP, настройки.

Все пути — под /api/assistant/ (см. apps/assistant/urls.py).
"""

from __future__ import annotations

import datetime as dt

from django.core.cache import cache
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_sendfile import sendfile
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistant import actions, knowledge, llm, mcp_client, web
from apps.assistant.agent import partial_key, start_run, title_from, tool_call_args
from apps.assistant.extract import ExtractError, extract_text, guess_mime
from apps.assistant.models import (
    AssistantSettings,
    Attachment,
    ContextMode,
    Conversation,
    KnowledgeCategory,
    KnowledgeFile,
    McpServer,
    MemoryItem,
    Message,
    PendingAction,
    Run,
    Scope,
)
from apps.core.models import Role

MAX_UPLOAD = 40 * 1024 * 1024
EDITOR_ROLES = {Role.OWNER, Role.HEAD, Role.METROLOGIST}
STALE_AFTER = dt.timedelta(minutes=30)


def is_editor(user) -> bool:
    return bool(user.is_superuser or getattr(user, "role", None) in EDITOR_ROLES)


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Сериализация
# ---------------------------------------------------------------------------
def attachment_json(a: Attachment) -> dict:
    return {
        "id": a.pk, "name": a.name, "mime": a.mime, "size": a.size, "origin": a.origin,
        "chars": len(a.text), "extract_error": a.extract_error, "is_image": a.is_image,
    }


def action_json(a: PendingAction) -> dict:
    return {
        "id": a.pk, "kind": a.kind, "title": a.title, "payload": a.payload, "sources": a.sources,
        "status": a.status, "result": a.result, "error": a.error,
        "decided_at": a.decided_at,
    }


def message_json(m: Message, actions_by_id: dict | None = None) -> dict:
    ui = dict(m.ui or {})
    action_id = ui.get("action")
    if action_id:
        action = (actions_by_id or {}).get(action_id) or PendingAction.objects.filter(pk=action_id).first()
        ui["action"] = action_json(action) if action else None
    return {
        "id": m.pk, "role": m.role, "content": m.content, "created_at": m.created_at,
        "tool_calls": tool_call_args(m), "tool_call_id": m.tool_call_id, "tool_name": m.tool_name,
        "ui": ui, "model": m.model, "usage": m.usage, "run": m.run_id,
        "attachments": [attachment_json(a) for a in m.attachments.all()],
    }


def messages_json(qs) -> list[dict]:
    messages = list(qs.prefetch_related("attachments"))
    ids = [m.ui.get("action") for m in messages if isinstance(m.ui, dict) and m.ui.get("action")]
    actions_by_id = {a.pk: a for a in PendingAction.objects.filter(pk__in=ids)}
    return [message_json(m, actions_by_id) for m in messages]


def run_json(run: Run | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.pk, "status": run.status, "activity": run.activity, "error": run.error,
        "model": run.model, "usage": run.usage, "active": run.is_active,
    }


def conversation_json(c: Conversation) -> dict:
    return {"id": c.pk, "title": c.title or "Новый чат", "model": c.model, "pinned": c.pinned,
            "updated_at": c.updated_at, "created_at": c.created_at}


def _reap_stale(conversation: Conversation) -> None:
    """Прогон, чей воркер умер (перезапуск контейнера), не должен висеть «в работе» вечно."""
    border = timezone.now() - STALE_AFTER
    Run.objects.filter(
        conversation=conversation, status__in=[Run.Status.QUEUED, Run.Status.RUNNING, Run.Status.CANCELLING],
        created_at__lt=border,
    ).update(status=Run.Status.ERROR, error="Прогон прервался (перезапуск сервера?) — отправьте сообщение ещё раз",
             finished_at=timezone.now())


def _conversation(request, pk) -> Conversation:
    return get_object_or_404(Conversation, pk=pk, user=request.user)


# ---------------------------------------------------------------------------
# Чаты
# ---------------------------------------------------------------------------
class ConversationListView(APIView):
    def get(self, request):
        qs = Conversation.objects.filter(user=request.user)
        query = (request.query_params.get("q") or "").strip()
        if query:
            qs = qs.filter(Q(title__icontains=query) | Q(messages__content__icontains=query)).distinct()
        return Response([conversation_json(c) for c in qs[:200]])

    def post(self, request):
        model = (request.data.get("model") or "").strip() or llm.default_model()
        conversation = Conversation.objects.create(user=request.user, model=model, title="")
        return Response(conversation_json(conversation), status=status.HTTP_201_CREATED)


class ConversationDetailView(APIView):
    def get(self, request, pk):
        conversation = _conversation(request, pk)
        _reap_stale(conversation)
        active = conversation.runs.filter(
            status__in=[Run.Status.QUEUED, Run.Status.RUNNING, Run.Status.CANCELLING]
        ).first()
        last = conversation.runs.first()
        return Response({
            "conversation": conversation_json(conversation),
            "messages": messages_json(conversation.messages.all()),
            "active_run": run_json(active),
            "last_run": run_json(last),
            "partial": cache.get(partial_key(active.pk)) if active else None,
        })

    def patch(self, request, pk):
        conversation = _conversation(request, pk)
        for field in ("title", "model"):
            if field in request.data:
                setattr(conversation, field, str(request.data[field])[:200])
        if "pinned" in request.data:
            conversation.pinned = bool(request.data["pinned"])
        conversation.save()
        return Response(conversation_json(conversation))

    def delete(self, request, pk):
        _conversation(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SendMessageView(APIView):
    """POST — сообщение человека + запуск прогона."""

    def post(self, request, pk):
        conversation = _conversation(request, pk)
        _reap_stale(conversation)
        if conversation.runs.filter(status__in=[Run.Status.QUEUED, Run.Status.RUNNING, Run.Status.CANCELLING]).exists():
            raise ValidationError("Ассистент ещё отвечает — дождитесь или остановите ответ")
        content = (request.data.get("content") or "").strip()
        attachment_ids = [_int(x) for x in request.data.get("attachment_ids") or []]
        if not content and not attachment_ids:
            raise ValidationError("Пустое сообщение")
        model = (request.data.get("model") or conversation.model or llm.default_model()).strip()
        if not model:
            raise ValidationError("Не настроена ни одна модель: задайте OPENROUTER_API_KEY или ключи Yandex")

        attachments = list(Attachment.objects.filter(pk__in=attachment_ids, user=request.user))
        message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=content)
        if attachments:
            for attachment in attachments:
                if attachment.conversation_id is None:
                    attachment.conversation = conversation
                    attachment.save(update_fields=["conversation"])
            message.attachments.set(attachments)
        if not conversation.title:
            conversation.title = title_from(content or attachments[0].name)
        conversation.model = model
        conversation.save()
        run = start_run(conversation, model)
        return Response({"message": message_json(message), "run": run_json(run)}, status=status.HTTP_201_CREATED)


class RegenerateView(APIView):
    """POST — удалить последний ответ ассистента и ответить заново (можно другой моделью)."""

    def post(self, request, pk):
        conversation = _conversation(request, pk)
        if conversation.runs.filter(status__in=[Run.Status.QUEUED, Run.Status.RUNNING, Run.Status.CANCELLING]).exists():
            raise ValidationError("Ассистент ещё отвечает")
        last_user = conversation.messages.filter(role=Message.Role.USER).order_by("-pk").first()
        if last_user is None:
            raise ValidationError("Нечего перегенерировать")
        conversation.messages.filter(pk__gt=last_user.pk).delete()
        model = (request.data.get("model") or conversation.model or llm.default_model()).strip()
        conversation.model = model
        conversation.save()
        run = start_run(conversation, model)
        return Response({"run": run_json(run)}, status=status.HTTP_201_CREATED)


class RunStateView(APIView):
    """GET ?after=<id> — новые сообщения прогона и недописанный текст. Экран чата опрашивает раз в ~0.5 с."""

    def get(self, request, pk):
        run = get_object_or_404(Run, pk=pk, conversation__user=request.user)
        after = _int(request.query_params.get("after"))
        messages = run.conversation.messages.filter(pk__gt=after)
        return Response({
            "run": run_json(run),
            "messages": messages_json(messages),
            "partial": cache.get(partial_key(run.pk)) if run.is_active else None,
        })


class RunCancelView(APIView):
    def post(self, request, pk):
        run = get_object_or_404(Run, pk=pk, conversation__user=request.user)
        if run.status == Run.Status.QUEUED:
            run.status = Run.Status.CANCELLED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
        elif run.status == Run.Status.RUNNING:
            run.status = Run.Status.CANCELLING
            run.save(update_fields=["status"])
        return Response(run_json(run))


class ActionDecideView(APIView):
    def post(self, request, pk, decision):
        action = get_object_or_404(PendingAction, pk=pk, conversation__user=request.user)
        try:
            actions.decide(action, request.user, confirm=decision == "confirm")
        except actions.ActionError as exc:
            raise ValidationError(str(exc)) from exc
        return Response(action_json(action))


# ---------------------------------------------------------------------------
# Файлы чата
# ---------------------------------------------------------------------------
class AttachmentUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError("Нет файла")
        if upload.size > MAX_UPLOAD:
            raise ValidationError("Файл больше 40 МБ")
        data = upload.read()
        mime = guess_mime(upload.name, upload.content_type or "")
        attachment = Attachment(user=request.user, name=upload.name[:255], mime=mime, size=len(data))
        conversation_id = _int(request.data.get("conversation"))
        if conversation_id:
            attachment.conversation = _conversation(request, conversation_id)
        if not mime.startswith("image/"):
            try:
                attachment.text = extract_text(data, upload.name, mime)
            except ExtractError as exc:
                attachment.extract_error = str(exc)[:500]
        upload.seek(0)
        attachment.file.save(upload.name, upload, save=False)
        attachment.save()
        return Response(attachment_json(attachment), status=status.HTTP_201_CREATED)


class AttachmentDownloadView(APIView):
    def get(self, request, pk):
        attachment = get_object_or_404(Attachment, pk=pk, user=request.user)
        return sendfile(request, attachment.file.path, attachment=not attachment.is_image,
                        attachment_filename=attachment.name, mimetype=attachment.mime or None)


# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------
class ModelsView(APIView):
    def get(self, request):
        models = llm.available_models()
        return Response({
            "default": llm.default_model(),
            "providers": {p: llm.provider_configured(p) for p in ("openrouter", "yandex")},
            "models": models,
            "notice": llm.models_notice(),
        })


# ---------------------------------------------------------------------------
# Контекст: файлы компании
# ---------------------------------------------------------------------------
def knowledge_json(kf: KnowledgeFile, user) -> dict:
    return {
        "id": kf.pk, "title": kf.title, "category": kf.category, "category_label": kf.get_category_display(),
        "description": kf.description, "scope": kf.scope, "mode": kf.mode, "original_name": kf.original_name,
        "mime": kf.mime, "chars": kf.chars, "chunks": getattr(kf, "chunk_count", None),
        "extract_error": kf.extract_error, "updated_at": kf.updated_at,
        "uploaded_by": (kf.uploaded_by.employee.full_name if kf.uploaded_by and hasattr(kf.uploaded_by, "employee")
                        else (kf.uploaded_by.email if kf.uploaded_by else "")),
        "can_edit": _can_edit_file(kf, user), "has_file": bool(kf.file),
    }


def _can_edit_file(kf: KnowledgeFile, user) -> bool:
    if kf.scope == Scope.PERSONAL:
        return kf.owner_id == user.pk
    return is_editor(user)


class KnowledgeListView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        qs = knowledge.visible_files(request.user).select_related("uploaded_by__employee").annotate(
            chunk_count=Count("chunks")
        )
        files = [knowledge_json(kf, request.user) for kf in qs]
        settings_obj = AssistantSettings.current()
        always_chars = sum(f["chars"] for f in files if f["mode"] == ContextMode.ALWAYS and not f["extract_error"])
        return Response({
            "files": files,
            "categories": [{"value": c.value, "label": c.label} for c in KnowledgeCategory],
            "always_chars": always_chars,
            "always_budget": settings_obj.always_budget_chars,
            "can_edit_company": is_editor(request.user),
        })

    def post(self, request):
        scope = request.data.get("scope") or Scope.COMPANY
        if scope == Scope.COMPANY and not is_editor(request.user):
            raise PermissionDenied("Общие файлы компании загружает метролог или руководитель — "
                                   "вы можете добавить личный файл")
        category = request.data.get("category") or KnowledgeCategory.OTHER
        if category not in KnowledgeCategory.values:
            raise ValidationError("Неизвестный раздел")
        mode = request.data.get("mode") or ContextMode.SEARCH
        if mode not in ContextMode.values:
            raise ValidationError("Неизвестный режим")
        upload = request.FILES.get("file")
        text = request.data.get("text") or ""
        if upload is None and not text.strip():
            raise ValidationError("Нужен файл или текст")
        if upload is not None and upload.size > MAX_UPLOAD:
            raise ValidationError("Файл больше 40 МБ")
        title = (request.data.get("title") or (upload.name.rsplit(".", 1)[0] if upload else "Заметка")).strip()[:250]
        kf = KnowledgeFile(
            title=title, category=category, mode=mode, scope=scope,
            description=(request.data.get("description") or "").strip(),
            owner=request.user if scope == Scope.PERSONAL else None, uploaded_by=request.user,
        )
        data = None
        if upload is not None:
            data = upload.read()
            upload.seek(0)
            kf.original_name = upload.name[:255]
            kf.mime = guess_mime(upload.name, upload.content_type or "")
            kf.file.save(upload.name, upload, save=False)
        else:
            kf.text = text.strip()
            kf.original_name = ""
        knowledge.index_file(kf, data)
        kf.chunk_count = kf.chunks.count()
        return Response(knowledge_json(kf, request.user), status=status.HTTP_201_CREATED)


class KnowledgeDetailView(APIView):
    def _get(self, request, pk) -> KnowledgeFile:
        return get_object_or_404(knowledge.visible_files(request.user), pk=pk)

    def get(self, request, pk):
        kf = self._get(request, pk)
        body = knowledge_json(kf, request.user)
        body["text_preview"] = kf.text[:20000]
        return Response(body)

    def patch(self, request, pk):
        kf = self._get(request, pk)
        if not _can_edit_file(kf, request.user):
            raise PermissionDenied("Общие файлы правит метролог или руководитель")
        for field in ("title", "description"):
            if field in request.data:
                setattr(kf, field, str(request.data[field]).strip()[: 250 if field == "title" else 5000])
        if "category" in request.data:
            if request.data["category"] not in KnowledgeCategory.values:
                raise ValidationError("Неизвестный раздел")
            kf.category = request.data["category"]
        if "mode" in request.data:
            if request.data["mode"] not in ContextMode.values:
                raise ValidationError("Неизвестный режим")
            kf.mode = request.data["mode"]
        reindex = False
        if "text" in request.data and not kf.file:
            kf.text = str(request.data["text"]).strip()
            reindex = True
        if reindex:
            knowledge.index_file(kf)
        else:
            kf.save()
        return Response(knowledge_json(kf, request.user))

    def delete(self, request, pk):
        kf = self._get(request, pk)
        if not _can_edit_file(kf, request.user):
            raise PermissionDenied("Общие файлы удаляет метролог или руководитель")
        if kf.file:
            kf.file.delete(save=False)
        kf.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class KnowledgeDownloadView(APIView):
    def get(self, request, pk):
        kf = get_object_or_404(knowledge.visible_files(request.user), pk=pk)
        if not kf.file:
            raise ValidationError("У этой записи нет файла — только текст")
        return sendfile(request, kf.file.path, attachment=True,
                        attachment_filename=kf.original_name or kf.title, mimetype=kf.mime or None)


class KnowledgeSearchView(APIView):
    """GET ?q= — тот же поиск, что у агента: проверить, найдёт ли он нужное."""

    def get(self, request):
        hits = knowledge.search(request.user, request.query_params.get("q") or "", limit=10)
        return Response([
            {"file_id": c.file_id, "file": c.file.title, "chunk": c.index, "section": c.heading,
             "score": round(score, 3), "text": c.text[:600]}
            for c, score in hits
        ])


# ---------------------------------------------------------------------------
# Память
# ---------------------------------------------------------------------------
def memory_json(m: MemoryItem, user) -> dict:
    return {
        "id": m.pk, "scope": m.scope, "text": m.text, "updated_at": m.updated_at,
        "source_conversation": m.source_conversation_id,
        "can_edit": m.user_id == user.pk if m.scope == Scope.PERSONAL else is_editor(user),
    }


class MemoryListView(APIView):
    def get(self, request):
        qs = MemoryItem.objects.filter(Q(scope=Scope.COMPANY) | Q(scope=Scope.PERSONAL, user=request.user))
        return Response({"items": [memory_json(m, request.user) for m in qs],
                         "can_edit_company": is_editor(request.user)})

    def post(self, request):
        text = (request.data.get("text") or "").strip()
        if not text:
            raise ValidationError("Пустая запись")
        scope = request.data.get("scope") or Scope.PERSONAL
        if scope == Scope.COMPANY and not is_editor(request.user):
            raise PermissionDenied("Общую память меняет метролог или руководитель")
        item = MemoryItem.objects.create(
            scope=scope, text=text, created_by=request.user,
            user=request.user if scope == Scope.PERSONAL else None,
        )
        return Response(memory_json(item, request.user), status=status.HTTP_201_CREATED)


class MemoryDetailView(APIView):
    def _get(self, request, pk) -> MemoryItem:
        item = get_object_or_404(
            MemoryItem.objects.filter(Q(scope=Scope.COMPANY) | Q(scope=Scope.PERSONAL, user=request.user)), pk=pk,
        )
        if not memory_json(item, request.user)["can_edit"]:
            raise PermissionDenied("Общую память меняет метролог или руководитель")
        return item

    def patch(self, request, pk):
        item = self._get(request, pk)
        text = (request.data.get("text") or "").strip()
        if not text:
            raise ValidationError("Пустая запись")
        item.text = text
        item.save()
        return Response(memory_json(item, request.user))

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# MCP-серверы и инструменты
# ---------------------------------------------------------------------------
def mcp_json(s: McpServer) -> dict:
    return {
        "id": s.pk, "name": s.name, "title": s.title, "url": s.url, "description": s.description,
        "enabled": s.enabled, "disabled_tools": s.disabled_tools,
        "has_auth": bool(s.headers),
    }


class McpListView(APIView):
    def get(self, request):
        from apps.assistant.tools import BUILTIN

        return Response({
            "servers": [mcp_json(s) for s in McpServer.objects.all()],
            "builtin": [{"name": t.name, "label": t.label, "description": t.description} for t in BUILTIN],
            "web_search": web.search_configured(),
            "can_edit": is_editor(request.user),
        })

    def post(self, request):
        if not is_editor(request.user):
            raise PermissionDenied("MCP-серверы подключает метролог или руководитель")
        name = (request.data.get("name") or "").strip()
        url = (request.data.get("url") or "").strip()
        if not name or not url:
            raise ValidationError("Нужны имя и адрес")
        if McpServer.objects.filter(name=name).exists():
            raise ValidationError("Сервер с таким именем уже есть")
        server = McpServer.objects.create(
            name=name, url=url, title=request.data.get("title") or "", description=request.data.get("description") or "",
            headers=_headers(request.data),
        )
        return Response(mcp_json(server), status=status.HTTP_201_CREATED)


def _headers(data) -> dict:
    token = (data.get("token") or "").strip()
    if token:
        return {"Authorization": token if " " in token else f"Bearer {token}"}
    headers = data.get("headers")
    return headers if isinstance(headers, dict) else {}


class McpDetailView(APIView):
    def _get(self, request, pk) -> McpServer:
        if not is_editor(request.user):
            raise PermissionDenied("MCP-серверы настраивает метролог или руководитель")
        return get_object_or_404(McpServer, pk=pk)

    def patch(self, request, pk):
        server = self._get(request, pk)
        for field in ("title", "url", "description"):
            if field in request.data:
                setattr(server, field, str(request.data[field]).strip())
        if "enabled" in request.data:
            server.enabled = bool(request.data["enabled"])
        if "disabled_tools" in request.data and isinstance(request.data["disabled_tools"], list):
            server.disabled_tools = [str(x) for x in request.data["disabled_tools"]]
        if request.data.get("token") or isinstance(request.data.get("headers"), dict):
            server.headers = _headers(request.data)
        server.save()
        return Response(mcp_json(server))

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class McpToolsView(APIView):
    """GET — подключиться к серверу и показать его инструменты (заодно проверка связи)."""

    def get(self, request, pk):
        server = get_object_or_404(McpServer, pk=pk)
        try:
            tools = mcp_client.server_tools(server, refresh=request.query_params.get("refresh") == "1")
        except mcp_client.McpError as exc:
            return Response({"ok": False, "error": str(exc), "tools": []})
        return Response({"ok": True, "tools": [
            {"name": t.get("name"), "title": t.get("title") or "", "description": (t.get("description") or "")[:500],
             "enabled": t.get("name") not in (server.disabled_tools or [])}
            for t in tools
        ]})


# ---------------------------------------------------------------------------
# Настройки и сводка контекста
# ---------------------------------------------------------------------------
class SettingsView(APIView):
    def get(self, request):
        s = AssistantSettings.current()
        totals = Run.objects.filter(conversation__user=request.user).aggregate(n=Count("id"))
        return Response({
            "default_model": s.default_model or llm.default_model(),
            "extra_instructions": s.extra_instructions,
            "max_steps": s.max_steps,
            "always_budget_chars": s.always_budget_chars,
            "can_edit": is_editor(request.user),
            "my_runs": totals["n"],
        })

    def patch(self, request):
        if not is_editor(request.user):
            raise PermissionDenied("Настройки ассистента меняет метролог или руководитель")
        s = AssistantSettings.current()
        if "default_model" in request.data:
            s.default_model = str(request.data["default_model"]).strip()[:200]
        if "extra_instructions" in request.data:
            s.extra_instructions = str(request.data["extra_instructions"])
        if "max_steps" in request.data:
            s.max_steps = min(max(_int(request.data["max_steps"], 30), 3), 80)
        if "always_budget_chars" in request.data:
            s.always_budget_chars = min(max(_int(request.data["always_budget_chars"], 120000), 0), 600_000)
        s.save()
        return self.get(request)


class UsageView(APIView):
    """GET — сколько потрачено (для руководителя — по всем, для остальных — свои)."""

    def get(self, request):
        since = timezone.now() - dt.timedelta(days=_int(request.query_params.get("days"), 30))
        qs = Message.objects.filter(role=Message.Role.ASSISTANT, created_at__gte=since)
        if not is_editor(request.user):
            qs = qs.filter(conversation__user=request.user)
        rows = {}
        for m in qs.values("model", "usage"):
            row = rows.setdefault(m["model"], {"model": m["model"], "calls": 0, "tokens": 0, "cost": 0.0})
            row["calls"] += 1
            row["tokens"] += int((m["usage"] or {}).get("total_tokens") or 0)
            row["cost"] = round(row["cost"] + float((m["usage"] or {}).get("cost") or 0), 4)
        return Response({"since": since, "rows": sorted(rows.values(), key=lambda r: -r["cost"])})

