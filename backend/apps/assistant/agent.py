"""Агентный цикл: модель → инструменты → модель … → ответ.

Прогон (``Run``) выполняется вне HTTP-запроса — в Celery (очередь
``assistant``), чтобы долгий поиск по ФГИС не держал веб-воркер и не
обрывался при перезагрузке страницы. Готовые сообщения сразу пишутся в БД,
недописанный текст ответа — в кэш (Redis), экран чата опрашивает прогон.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.assistant import llm
from apps.assistant.models import AssistantSettings, Conversation, Message, Run
from apps.assistant.prompt import build_system_prompt
from apps.assistant.tools import MAX_RESULT, ToolContext, ToolFailure, all_tools

logger = logging.getLogger(__name__)

INLINE_FILE_CHARS = 12_000


def partial_key(run_id: int) -> str:
    return f"assistant:run:{run_id}:partial"


# ---------------------------------------------------------------------------
# История чата → сообщения для модели
# ---------------------------------------------------------------------------
def _image_part(attachment) -> dict | None:
    try:
        from PIL import Image

        attachment.file.open("rb")
        try:
            image = Image.open(attachment.file)
            image.load()
        finally:
            attachment.file.close()
        image = image.convert("RGB")
        image.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось подготовить картинку %s", attachment.pk, exc_info=True)
        return None
    data = base64.b64encode(buffer.getvalue()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def _user_content(message: Message, vision: bool):
    text = message.content or ""
    images = []
    notes = []
    for attachment in message.attachments.all():
        if attachment.is_image:
            if vision:
                part = _image_part(attachment)
                if part:
                    images.append(part)
                    notes.append(f"[Картинка: {attachment.name}, attachment_id={attachment.pk}]")
            else:
                notes.append(
                    f"[Картинка {attachment.name} — выбранная модель не видит изображения; "
                    "предложи человеку выбрать модель с поддержкой картинок]"
                )
        elif attachment.text and len(attachment.text) <= INLINE_FILE_CHARS:
            notes.append(f"[Файл {attachment.name}, attachment_id={attachment.pk}]\n{attachment.text}\n[конец файла]")
        elif attachment.text:
            notes.append(
                f"[Файл {attachment.name}, attachment_id={attachment.pk}, {len(attachment.text)} символов — "
                "читай attachment_read]"
            )
        else:
            notes.append(f"[Файл {attachment.name}, attachment_id={attachment.pk} — текст не извлечён: "
                         f"{attachment.extract_error or 'пусто'}]")
    if notes:
        text = (text + "\n\n" + "\n\n".join(notes)).strip()
    if images:
        return [{"type": "text", "text": text or "(см. изображения)"}, *images]
    return text


REASONING_TEXT_LIMIT = 40_000


def _reasoning_payload(completion: llm.Completion) -> dict:
    out: dict = {}
    if completion.reasoning_details:
        out["details"] = completion.reasoning_details
    if completion.reasoning:
        out["text"] = completion.reasoning[:REASONING_TEXT_LIMIT]
    return out


def _attach_reasoning(item: dict, message: Message) -> None:
    """Рассуждения — обратно модели, как пришли (OpenRouter: reasoning_details, иначе текстом)."""
    reasoning = message.reasoning or {}
    if reasoning.get("details"):
        item["reasoning_details"] = reasoning["details"]
    elif reasoning.get("text"):
        item["reasoning"] = reasoning["text"]


def build_messages(conversation: Conversation, *, vision: bool, model: str = "") -> list[dict]:
    """История для модели.

    ``model`` — модель прогона: её рассуждения из текущего хода (после последнего сообщения
    человека) уходят обратно. Чужие (чат переключили на другую модель) и прошлых ходов — нет:
    подписи блоков у другого провайдера невалидны, а старые рассуждения только тратят токены.
    """
    send_reasoning = bool(model) and llm.split_model(model)[0] == "openrouter"
    messages = list(conversation.messages.prefetch_related("attachments").order_by("pk"))
    last_user = max((m.pk for m in messages if m.role == Message.Role.USER), default=0)
    out: list[dict] = []
    pending: list[str] = []  # tool_call_id, на которые ещё нет результата
    deferred: list[dict] = []  # события, пришедшие посреди серии вызовов инструментов

    def close_pending():
        for call_id in pending:
            out.append({"role": "tool", "tool_call_id": call_id, "content": "Не выполнено: прогон был прерван."})
        pending.clear()

    def flush_deferred():
        if not pending and deferred:
            out.extend(deferred)
            deferred.clear()

    for message in messages:
        if message.role == Message.Role.TOOL:
            if message.tool_call_id in pending:
                pending.remove(message.tool_call_id)
                out.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content})
            flush_deferred()
            continue
        if message.role == Message.Role.EVENT:
            item = {"role": "user", "content": message.content}
            (deferred if pending else out).append(item)
            continue
        close_pending()
        flush_deferred()
        if message.role == Message.Role.USER:
            out.append({"role": "user", "content": _user_content(message, vision)})
        elif message.role == Message.Role.ASSISTANT:
            item: dict = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                item["tool_calls"] = message.tool_calls
                pending.extend(call["id"] for call in message.tool_calls)
            if item["content"] is None and not message.tool_calls:
                continue  # пустой ответ (сбой модели) — в историю не идёт, чтобы модель его не повторяла
            if send_reasoning and message.pk > last_user and message.model == model:
                _attach_reasoning(item, message)
            out.append(item)
    close_pending()
    flush_deferred()
    return out


# ---------------------------------------------------------------------------
# Прогон
# ---------------------------------------------------------------------------
def _add_usage(total: dict, usage: dict) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
        if usage.get(key) is not None:
            total[key] = round(total.get(key, 0) + float(usage[key]), 6)


def execute_run(run_id: int, *, extra_tools: list | None = None) -> None:
    with transaction.atomic():
        run = Run.objects.select_for_update().select_related("conversation__user").get(pk=run_id)
        if run.status != Run.Status.QUEUED:
            return
        run.status = Run.Status.RUNNING
        run.save(update_fields=["status"])

    conversation = run.conversation
    user = conversation.user
    try:
        _loop(run, conversation, user, extra_tools=extra_tools)
        run.refresh_from_db(fields=["status"])
        run.status = Run.Status.CANCELLED if run.status == Run.Status.CANCELLING else Run.Status.DONE
    except llm.LLMError as exc:
        run.status, run.error = Run.Status.ERROR, str(exc)
    except Exception as exc:  # noqa: BLE001 — прогон не должен зависнуть в «работает»
        logger.exception("Прогон ассистента %s упал", run_id)
        run.status, run.error = Run.Status.ERROR, f"Внутренняя ошибка: {exc}"
    finally:
        cache.delete(partial_key(run.pk))
        run.activity = ""
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "activity", "finished_at", "usage"])
        Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())


EMPTY_NUDGE = ("[Система] Твой предыдущий ответ пришёл пустым — человек ничего не увидел. "
               "Продолжи: вызови нужный инструмент или напиши ответ человеку текстом.")


def _is_empty(completion: llm.Completion) -> bool:
    return (completion.finish_reason != "cancelled" and not completion.tool_calls
            and not (completion.text or "").strip())


def _empty_reason(completion: llm.Completion) -> str:
    if completion.finish_reason == "length":
        return ("Модель исчерпала лимит длины ответа, не написав текста (скорее всего, ушла в долгие "
                "рассуждения). Повторите вопрос или выберите другую модель.")
    if completion.reasoning:
        return ("Модель дважды вернула только рассуждения без ответа. Похоже, она плохо работает "
                "с инструментами — выберите другую модель и повторите вопрос.")
    return "Модель дважды вернула пустой ответ. Повторите вопрос или выберите другую модель."


def _loop(run: Run, conversation: Conversation, user, *, extra_tools: list | None = None) -> None:
    config = AssistantSettings.current()
    tools, mcp_errors = all_tools()
    # Фоновая работа (шаблон для нового типа СИ) добавляет свои инструменты, которых нет в чате.
    tools.update({tool.name: tool for tool in extra_tools or []})
    # Чат режима «Создать шаблон протокола поверки» — свои инструменты черновика и раздел промта о типе СИ.
    from apps.assistant import template_workspace

    tools.update({tool.name: tool for tool in template_workspace.tools_for(conversation)})
    schemas = [tool.schema() for tool in tools.values()]
    system = build_system_prompt(user, tool_names=list(tools), mcp_errors=mcp_errors)
    workspace_section = template_workspace.prompt_for(conversation)
    if workspace_section:
        system += "\n\n" + workspace_section
    vision = bool(llm.model_info(run.model).get("vision"))
    ctx = ToolContext(user=user, conversation=conversation, run=run)

    last_check = [0.0]
    cancelled = [False]

    def should_stop() -> bool:
        now = time.monotonic()
        if now - last_check[0] > 1.0:
            last_check[0] = now
            cancelled[0] = Run.objects.filter(pk=run.pk, status=Run.Status.CANCELLING).exists()
        return cancelled[0]

    def set_activity(text: str) -> None:
        Run.objects.filter(pk=run.pk).update(activity=text[:300])

    for step in range(config.max_steps + 1):
        if should_stop():
            return
        final_step = step == config.max_steps
        messages = [{"role": "system", "content": system}, *build_messages(conversation, vision=vision, model=run.model)]
        if final_step:
            messages.append({
                "role": "user",
                "content": "[Система] Лимит шагов исчерпан. Подведи итог: что найдено (с источниками), "
                           "что не удалось и что нужно от человека. Инструменты больше не вызывай.",
            })

        buffer: list[str] = []
        last_flush = [0.0]

        def on_text(chunk: str) -> None:
            buffer.append(chunk)
            now = time.monotonic()
            if now - last_flush[0] > 0.25:
                last_flush[0] = now
                cache.set(partial_key(run.pk), "".join(buffer), 600)

        set_activity("Думаю…")
        completion = llm.chat(
            run.model, messages, tools=None if final_step else schemas,
            on_text=on_text, should_stop=should_stop,
        )
        _add_usage(run.usage, completion.usage)
        if _is_empty(completion):
            # Часть моделей (DeepSeek и др. «думающие») иногда отдают только рассуждения без текста
            # ответа — раньше прогон тихо завершался «готово» с пустым сообщением. Даём ещё одну попытку.
            logger.warning("Прогон %s: пустой ответ модели %s (finish_reason=%s, reasoning=%d симв.)",
                           run.pk, run.model, completion.finish_reason, len(completion.reasoning))
            set_activity("Модель ответила пусто — переспрашиваю…")
            completion = llm.chat(
                run.model, [*messages, {"role": "user", "content": EMPTY_NUDGE}],
                tools=None if final_step else schemas, on_text=on_text, should_stop=should_stop,
            )
            _add_usage(run.usage, completion.usage)
            Run.objects.filter(pk=run.pk).update(usage=run.usage)
            if _is_empty(completion):
                raise llm.LLMError(_empty_reason(completion))
        tool_calls = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments or "{}"}}
            for c in completion.tool_calls
        ]
        Message.objects.create(
            conversation=conversation, run=run, role=Message.Role.ASSISTANT,
            content=completion.text, tool_calls=tool_calls, model=run.model, usage=completion.usage,
            reasoning=_reasoning_payload(completion),
        )
        cache.delete(partial_key(run.pk))
        Run.objects.filter(pk=run.pk).update(usage=run.usage)
        if not completion.tool_calls or completion.finish_reason == "cancelled":
            return

        for call in completion.tool_calls:
            tool = tools.get(call.name)
            label = tool.label if tool else call.name
            set_activity(label)
            args: dict = {}
            ui: dict = {"label": label}
            try:
                if tool is None:
                    raise ToolFailure(f"Нет инструмента {call.name}")
                args = call.parsed()
                result = tool.handler(ctx, args)
                content, is_error = result.content, result.is_error
                ui.update(result.ui)
            except (ToolFailure, ValueError) as exc:
                content, is_error = f"Ошибка: {exc}", True
            except Exception as exc:  # noqa: BLE001
                logger.exception("Инструмент %s упал", call.name)
                content, is_error = f"Внутренняя ошибка инструмента: {exc}", True
            if len(content) > MAX_RESULT:
                content = content[:MAX_RESULT] + f"\n\n[ответ обрезан: показано {MAX_RESULT} из {len(content)} символов — сузь запрос]"
            ui["args"] = args
            ui["error"] = is_error
            if is_error:
                ui["summary"] = content[:300]
            Message.objects.create(
                conversation=conversation, run=run, role=Message.Role.TOOL,
                content=content, tool_call_id=call.id, tool_name=call.name, ui=ui,
            )


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
def start_run(conversation: Conversation, model: str) -> Run:
    run = Run.objects.create(conversation=conversation, model=model)
    mode = getattr(settings, "ASSISTANT_RUN_MODE", "celery")
    if mode == "sync":
        execute_run(run.pk)
        run.refresh_from_db()
    elif mode == "thread":
        transaction.on_commit(lambda: threading.Thread(target=execute_run, args=(run.pk,), daemon=True).start())
    else:
        from apps.assistant.tasks import run_agent

        transaction.on_commit(lambda: _enqueue(run, run_agent))
    return run


def _enqueue(run: Run, task) -> None:
    """Поставить прогон в очередь; не вышло (Redis лежит) — прогон сразу ошибочный, с причиной.

    Без этого исключение брокера уходило из запроса голой «Ошибкой 500», а сообщение человека
    уже было сохранено — и чат выглядел так, будто ассистент просто молчит.
    """
    try:
        task.delay(run.pk)
    except Exception as exc:  # noqa: BLE001 — kombu/redis кидают разное, итог один
        logger.exception("Не удалось поставить прогон %s в очередь", run.pk)
        from apps.assistant import health

        health.forget()
        run.status = Run.Status.ERROR
        run.error = (f"Ассистент не работает: не удалось поставить задачу в очередь ({type(exc).__name__}). "
                     "Скорее всего, не запущен Redis — сообщите администратору.")
        run.finished_at = timezone.now()
        Run.objects.filter(pk=run.pk).update(status=run.status, error=run.error, finished_at=run.finished_at)


def title_from(text: str) -> str:
    line = " ".join((text or "").split())
    return (line[:70] + "…") if len(line) > 70 else (line or "Новый чат")


def tool_call_args(message: Message) -> list[dict]:
    """Для отображения: имя и аргументы каждого вызова."""
    out = []
    for call in message.tool_calls or []:
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
        except (ValueError, KeyError):
            args = {}
        out.append({"id": call.get("id"), "name": call.get("function", {}).get("name"), "args": args})
    return out
