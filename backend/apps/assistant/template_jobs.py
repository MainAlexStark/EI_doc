"""Шаблоны для нового типа СИ — работа агента в фоне.

Поверитель или метролог выбирает тип из Госреестра — EI_doc заводит тип и сразу ставит
``TemplateJob``. Воркер отправляет агенту (сервис agentkit, ``POST /api/run``) задачу от имени
того, кто завёл тип: собрать шаблон поверки по методике поверки и описанию типа, подобрать
строки «Перечня применяемых эталонов», а если это испытательное оборудование — ещё и шаблон
аттестации. Агент сохраняет результат инструментом ``ei_template_job_save`` — только
черновиком (``needs_review``): протокол по нему не подпишется, пока метролог не проверит шаблон.

Человек ничего не ждёт: статус виден в карточке типа («агент готовит шаблон…» → «готов, ждёт
проверки»), там же — «Переделать» с пожеланием. Разговор агента виден во вкладке «Ассистент».
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.assistant.models import TemplateJob

log = logging.getLogger(__name__)

RUN_TIMEOUT = 24 * 60  # секунд — чуть меньше лимита задачи Celery
WATER_CALCULATOR = "water_meter"


class JobError(RuntimeError):
    pass


def configured() -> bool:
    """Подключён сервис агента (agentkit) — работа уходит ему."""
    return bool(settings.AGENT_INTERNAL_URL and settings.AGENT_SERVICE_TOKEN)


def builtin_model() -> str:
    """Модель встроенного ассистента EI_doc — запасной путь, если сервиса агента нет. Пусто — нет и его."""
    from apps.assistant import llm

    model = llm.default_model()
    return model if model and llm.provider_configured(llm.split_model(model)[0]) else ""


def available() -> bool:
    return configured() or bool(builtin_model())


def latest(si_type) -> TemplateJob | None:
    return si_type.template_jobs.order_by("-created_at").first()


def job_dict(job: TemplateJob | None) -> dict | None:
    if job is None:
        return None
    vt, ap = job.verification_template, job.attestation_program
    return {
        "id": job.pk, "status": job.status, "status_display": job.get_status_display(),
        "comment": job.comment, "summary": job.summary, "error": job.error,
        "created_at": job.created_at, "finished_at": job.finished_at,
        "verification_template": {"id": vt.pk, "title": vt.title, "needs_review": vt.needs_review} if vt else None,
        "attestation_program": {"id": ap.pk, "title": ap.title, "needs_review": ap.needs_review} if ap else None,
        "session_id": job.session_id,
    }


def start(si_type, user, *, comment: str = "") -> TemplateJob:
    """Поставить работу. Если по типу уже идёт — вернуть её, вторую не ставить."""
    open_job = si_type.template_jobs.filter(status__in=[TemplateJob.Status.QUEUED, TemplateJob.Status.RUNNING]).first()
    if open_job is not None:
        return open_job
    prev = latest(si_type)
    job = TemplateJob.objects.create(si_type=si_type, requested_by=user, comment=(comment or "").strip()[:4000])
    if prev is not None:
        # Переделка: свой черновик агента обновляется на месте, а не плодит копии.
        vt, ap = prev.verification_template, prev.attestation_program
        job.verification_template = vt if vt and vt.needs_review and vt.created_via_assistant else None
        job.attestation_program = ap if ap and ap.needs_review and ap.created_via_assistant else None
        job.save(update_fields=["verification_template", "attestation_program"])
    if si_type.family.calculator_key == WATER_CALCULATOR and not job.comment:
        _finish(job, TemplateJob.Status.SKIPPED,
                summary="Счётчики воды считает встроенный калькулятор EI_doc — отдельный шаблон поверки не нужен.")
        return job
    if not available():
        _finish(job, TemplateJob.Status.FAILED,
                error="Ассистент не подключён: в .env EI_doc нет ни сервиса агента (AGENT_SERVICE_TOKEN), "
                      "ни ключа модели для встроенного ассистента (OPENROUTER_API_KEY или YANDEX_VISION_*). "
                      "Шаблон можно завести вручную.")
        return job
    _enqueue(job.pk)
    return job


def _enqueue(job_id: int) -> None:
    mode = getattr(settings, "ASSISTANT_RUN_MODE", "celery")
    if mode == "sync":
        transaction.on_commit(lambda: run(job_id))
    elif mode == "thread":
        transaction.on_commit(lambda: threading.Thread(target=run, args=(job_id,), daemon=True).start())
    else:
        def send():
            try:
                from apps.assistant.tasks import run_template_job

                run_template_job.delay(job_id)
            except Exception as exc:  # noqa: BLE001 — нет брокера: тип заведён, а работа — с понятной ошибкой
                log.warning("Не удалось поставить работу шаблона %s: %s", job_id, exc)
                job = TemplateJob.objects.filter(pk=job_id).first()
                if job:
                    _finish(job, TemplateJob.Status.FAILED, error=f"Очередь задач недоступна: {exc}")

        transaction.on_commit(send)


def _finish(job: TemplateJob, status: str, *, summary: str = "", error: str = "") -> None:
    job.status = status
    job.summary = (summary or job.summary)[:20000]
    job.error = error[:4000]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "summary", "error", "finished_at"])


def task_text(job: TemplateJob) -> str:
    """Задача для агента. Он работает один, без человека: вопросов не задаёт, решает сам и пишет допущения."""
    from apps.arshin.models import FifDocument
    from apps.verification.models import VerificationTemplate

    t = job.si_type
    docs = []
    if t.fif_uuid:
        docs = [f"{d.get_kind_display()}: «{d.title}»{'' if d.file else ' (ещё не скачан)'}"
                for d in FifDocument.objects.filter(mit_uuid=t.fif_uuid)]
    existing = VerificationTemplate.objects.filter(si_type=t, is_active=True).first() or VerificationTemplate.objects.filter(
        family=t.family, si_type__isnull=True, is_active=True).first()
    lines = [
        f"ФОНОВАЯ РАБОТА EI_doc № {job.pk} (job_id={job.pk}): шаблоны для нового типа СИ. Человека рядом нет — "
        "НЕ задавай вопросов (ask_user не вызывай), решай сам, допущения перечисли в итоге.",
        "",
        "Тип СИ (уже заведён в справочнике EI_doc):",
        f"- id в EI_doc: {t.pk}; наименование: {t.name}; номер в Госреестре: {t.registry_number}",
        f"- наименование типа: {t.type_title or '—'}; изготовитель: {t.manufacturer or '—'}",
        f"- семейство EI_doc: {t.family.name} (id {t.family_id}); МПИ {t.verification_interval_months} мес.",
        f"- mit_uuid в ФГИС: {t.fif_uuid or '—'}; характеристики типа (limits): {t.limits or '—'}",
    ]
    if docs:
        lines.append("- документы типа в EI_doc (читай ei_fif_document_read si_type=" + str(t.pk) + "): " + "; ".join(docs))
    if existing and existing.pk != job.verification_template_id:
        lines.append(f"- уже есть шаблон поверки «{existing.title}» (id {existing.pk}) для этого семейства — "
                     "посмотри его (ei_verification_templates full=true): если подходит этому типу как есть, "
                     "не делай копию, а сохрани с template_id этого шаблона только kits, если их там нет")
    lines += [
        "",
        "Что сделать:",
        "1. Методика поверки и описание типа: ei_fif_document_read (kind=meth, затем spec). Нет файла — get_type "
        "во ФГИС и web_fetch по ссылке. Обозначение методики — из карточки типа.",
        "2. Перечень применяемых эталонов компании: ei_standard_kits (query — вид СИ, например «манометр», "
        "«весы», «гигрометр»). Выбери строки, подходящие этому типу (по виду СИ и диапазону/классу), их id — в kits. "
        "Подходящих строк нет — kits не передавай и напиши в итоге, что метрологу нужно добавить набор в перечень.",
        "3. Шаблон поверки по методике (формат — в описании ei_template_job_save / ei_verification_template_propose): "
        "операции поверки, поверяемые точки, формулы и допуски с пунктами методики, средства поверки — из перечня. "
        "Проверь его ei_verification_template_check, если в методике есть пример. "
        "Сохрани: ei_template_job_save job_id=" + str(job.pk) + " kind=verification.",
        "4. Если по карточке типа и описанию это ИСПЫТАТЕЛЬНОЕ ОБОРУДОВАНИЕ (камера, термостат, шкаф, ванна, печь — "
        "то, что аттестуют по ГОСТ Р 8.568), сделай ещё шаблон аттестации и сохрани ei_template_job_save "
        "kind=attestation. Для обычного СИ — не нужно.",
        "5. Итог — 3–6 строк: что вошло, откуда (документ, пункты), какие строки перечня взял, допущения и что "
        "проверить метрологу. Не сохраняй ничего другими инструментами (*_propose здесь не нужны).",
    ]
    if job.comment:
        lines += ["", f"Пожелание человека к шаблону (переделка): {job.comment}"]
    if job.verification_template_id:
        lines.append(f"Прошлый черновик шаблона поверки (id {job.verification_template_id}) уже привязан к этой работе: "
                     "посмотри его (ei_verification_templates full=true), поправь и сохрани ei_template_job_save "
                     "kind=verification БЕЗ template_id — он обновится на месте.")
    if job.attestation_program_id:
        lines.append(f"Прошлый черновик шаблона аттестации (id {job.attestation_program_id}) тоже привязан — "
                     "сохранение kind=attestation обновит его.")
    return "\n".join(lines)


def run(job_id: int) -> None:
    job = TemplateJob.objects.select_related("si_type__family", "requested_by").filter(pk=job_id).first()
    if job is None or job.status != TemplateJob.Status.QUEUED:
        return
    job.status = TemplateJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    user = job.requested_by
    if user is not None and not configured():
        _run_builtin(job, user)
        return
    try:
        if user is None:
            raise JobError("Нет пользователя, от имени которого работать")
        response = requests.post(
            settings.AGENT_INTERNAL_URL.rstrip("/") + "/api/run",
            json={"text": task_text(job)},
            headers={"Authorization": f"Bearer {settings.AGENT_SERVICE_TOKEN}", "X-Agent-User-Id": quote(str(user.pk))},
            timeout=RUN_TIMEOUT,
        )
        if response.status_code >= 400:
            raise JobError(f"Агент ответил {response.status_code}: {response.text[:500]}")
        body = response.json()
    except (requests.RequestException, ValueError, JobError) as exc:
        job.refresh_from_db()
        if job.saved_at:
            _finish(job, TemplateJob.Status.DONE, summary=job.summary, error=f"Агент не дописал итог: {exc}")
        else:
            _finish(job, TemplateJob.Status.FAILED, error=str(exc))
        return

    job.refresh_from_db()
    job.session_id = str(body.get("session_id") or "")[:120]
    job.save(update_fields=["session_id"])
    text = str(body.get("text") or "").strip()
    if job.saved_at:
        _finish(job, TemplateJob.Status.DONE, summary=text)
    else:
        _finish(job, TemplateJob.Status.FAILED, summary=text,
                error="Агент закончил, но шаблон не сохранил" + (f" (статус прогона: {body.get('status')})"
                                                                if body.get("status") not in (None, "done") else ""))


def _run_builtin(job: TemplateJob, user) -> None:
    """Сервиса агента нет — ту же задачу выполняет встроенный ассистент EI_doc (его агентный цикл).

    Разговор виден во вкладке «Ассистент» (встроенный чат), инструмент сохранения — тот же
    ``ei_template_job_save``, что и у сервиса агента, поэтому правила черновика одинаковые.
    """
    from apps.assistant import agent, host
    from apps.assistant.models import Conversation, Message, Run

    model = builtin_model()
    if not model:
        _finish(job, TemplateJob.Status.FAILED, error="Ассистент не подключён: нет модели для встроенного ассистента")
        return
    conversation = Conversation.objects.create(user=user, model=model,
                                               title=agent.title_from(f"Шаблон поверки: {job.si_type.name}"))
    Message.objects.create(conversation=conversation, role=Message.Role.USER, content=task_text(job))
    run = Run.objects.create(conversation=conversation, model=model)
    job.session_id = f"conv:{conversation.pk}"
    job.save(update_fields=["session_id"])
    save_tool = next(t for t in host.HOST_ONLY if t.name == "ei_template_job_save")
    try:
        agent.execute_run(run.pk, extra_tools=[save_tool])
    except Exception as exc:  # noqa: BLE001 — работа не должна зависнуть в «агент работает»
        log.exception("Встроенный ассистент упал на работе %s", job.pk)
        run.error = str(exc)
    run.refresh_from_db()
    job.refresh_from_db()
    last = conversation.messages.filter(role=Message.Role.ASSISTANT).exclude(content="").order_by("-pk").first()
    text = (last.content if last else "").strip()
    if job.saved_at:
        _finish(job, TemplateJob.Status.DONE, summary=text,
                error=f"Ассистент не дописал итог: {run.error}" if run.error else "")
    else:
        _finish(job, TemplateJob.Status.FAILED, summary=text,
                error=f"Ассистент не смог: {run.error}" if run.error else "Ассистент закончил, но шаблон не сохранил")


def reap_stale(minutes: int = 40) -> int:
    """Работы, «зависшие» после падения воркера, — в ошибку, чтобы можно было перезапустить."""
    edge = timezone.now() - dt.timedelta(minutes=minutes)
    stale = TemplateJob.objects.filter(status__in=[TemplateJob.Status.QUEUED, TemplateJob.Status.RUNNING],
                                       created_at__lt=edge)
    count = 0
    for job in stale:
        _finish(job, TemplateJob.Status.FAILED, error="Работа прервалась (перезапуск сервера?) — нажмите «Переделать»")
        count += 1
    return count
