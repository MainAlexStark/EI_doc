"""Действия, которые агент подготовил, а человек подтверждает кнопкой.

Выполняются от имени того, кто нажал «Подтвердить», с его правами — ровно
так же, как если бы он сделал это руками на своём экране. Результат
записывается событием в чат: агент увидит его в следующем ответе.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.assistant.models import Message, MemoryItem, PendingAction, Scope


class ActionError(Exception):
    pass


def _si_type_create(action: PendingAction, user) -> dict:
    from apps.catalog.api_types import SiTypeSerializer, SiTypeWriteSerializer, can_create, is_reviewer
    from apps.catalog.models import SiType

    if not can_create(user):
        raise PermissionDenied("Заводить типы СИ может поверитель, метролог или руководитель")
    payload = dict(action.payload)
    serializer = SiTypeWriteSerializer(data=payload)
    if not serializer.is_valid():
        raise ActionError(f"Данные не прошли проверку: {serializer.errors}")
    data = serializer.validated_data
    existing = SiType.objects.filter(registry_number=data["registry_number"], name=data["name"]).first()
    if existing:
        return {"id": existing.pk, "existing": True, "label": SiTypeSerializer(existing).data["label"]}
    reviewer = is_reviewer(user)
    fields = dict(
        family=data["family"], registry_number=data["registry_number"], name=data["name"],
        manufacturer=data.get("manufacturer", "").strip(), limits=data.get("limits") or {},
        fif_url=data.get("fif_url", ""), fif_raw={"assistant_sources": action.sources},
        created_by=user, needs_review=not reviewer,
        reviewed_by=user if reviewer else None, reviewed_at=timezone.now() if reviewer else None,
    )
    if data.get("verification_interval_months"):
        fields["verification_interval_months"] = data["verification_interval_months"]
    try:
        with transaction.atomic():
            si_type = SiType.objects.create(**fields)
    except IntegrityError:
        si_type = SiType.objects.get(registry_number=data["registry_number"], name=data["name"])
    return {
        "id": si_type.pk, "existing": False, "needs_review": si_type.needs_review,
        "label": SiTypeSerializer(si_type).data["label"],
    }


def _can_edit_company_memory(user) -> bool:
    from apps.catalog.api_types import is_reviewer

    return is_reviewer(user)


def _memory_company_save(action: PendingAction, user) -> dict:
    if not _can_edit_company_memory(user):
        raise PermissionDenied("Общую память компании меняет метролог или руководитель")
    item = MemoryItem.objects.create(
        scope=Scope.COMPANY, text=action.payload["text"], created_by=user,
        source_conversation=action.conversation,
    )
    return {"id": item.pk}


def _memory_company_delete(action: PendingAction, user) -> dict:
    if not _can_edit_company_memory(user):
        raise PermissionDenied("Общую память компании меняет метролог или руководитель")
    deleted, _ = MemoryItem.objects.filter(pk=action.payload["memory_id"], scope=Scope.COMPANY).delete()
    return {"deleted": bool(deleted)}


def _attestation_create(action: PendingAction, user) -> dict:
    from apps.equipment import service
    from apps.equipment.models import AttestationProgram

    payload = dict(action.payload)
    program = AttestationProgram.objects.filter(pk=payload.pop("program_id", None) or 0).first()
    plan_check = payload.pop("plan_check", None)
    try:
        att = service.create(user, payload, via_assistant=True, sources=action.sources, program=program,
                             plan_check=plan_check)
    except service.AttestationError as exc:
        raise ActionError(str(exc)) from exc
    return {"id": att.pk, "plan_approved": att.plan_approved, "label": str(att)}


def _attestation_template_save(action: PendingAction, user) -> dict:
    from apps.equipment import service

    payload = dict(action.payload)
    try:
        program = service.save_program(
            user, payload, sources=action.sources, plan_check=payload.get("plan_check"),
            program_id=payload.get("program_id"),
        )
    except service.AttestationError as exc:
        raise ActionError(str(exc)) from exc
    return {"id": program.pk, "title": program.title, "needs_review": program.needs_review,
            "updated": bool(payload.get("program_id"))}


def _verification_template_save(action: PendingAction, user) -> dict:
    from apps.verification import templates

    payload = dict(action.payload)
    try:
        template = templates.save_template(
            user, payload, sources=action.sources, plan_check=payload.get("plan_check"),
            template_id=payload.get("template_id"), via_assistant=True,
        )
    except templates.TemplateError as exc:
        raise ActionError(str(exc)) from exc
    return {"id": template.pk, "title": template.title, "needs_review": template.needs_review,
            "updated": bool(payload.get("template_id"))}


def _standard_kits_update(action: PendingAction, user) -> dict:
    from apps.catalog import standard_kits
    from apps.catalog.api_types import is_reviewer

    if not is_reviewer(user):
        raise ActionError("Перечень эталонов меняет метролог или руководитель")
    try:
        done = standard_kits.apply_changes(action.payload.get("changes"), user)
    except standard_kits.KitError as exc:
        raise ActionError(str(exc)) from exc
    return {"done": done}


EXECUTORS = {
    "standard_kits_update": _standard_kits_update,
    "attestation_create": _attestation_create,
    "attestation_template_save": _attestation_template_save,
    "verification_template_save": _verification_template_save,
    "si_type_create": _si_type_create,
    "memory_company_save": _memory_company_save,
    "memory_company_delete": _memory_company_delete,
}

DONE_TEXT = {
    "standard_kits_update": lambda r: "Перечень эталонов обновлён: " + "; ".join(r.get("done") or []) + ".",
    "attestation_create": lambda r: (
        f"Аттестация заведена (id {r['id']}): {r['label']}. Измерения и условия человек вносит на вкладке "
        "«Аттестация ИО»" + ("." if r.get("plan_approved") else "; план ждёт проверки метролога.")
    ),
    "attestation_template_save": lambda r: (
        f"Шаблон аттестации {'обновлён' if r.get('updated') else 'заведён'} (id {r['id']}): «{r['title']}». Он есть "
        "в списке «Новая аттестация» на вкладке «Аттестация ИО»: человек выбирает шаблон, вписывает оборудование и "
        "вносит измерения" + ("; шаблон ждёт проверки метролога." if r.get("needs_review") else ".")
    ),
    "verification_template_save": lambda r: (
        f"Шаблон поверки {'обновлён' if r.get('updated') else 'заведён'} (id {r['id']}): «{r['title']}». Поверитель "
        "проводит по нему поверку на экране «Поверки»: параметры прибора, отметки по операциям, показания — "
        "расчёт и протокол делает EI_doc" + ("; шаблон ждёт проверки метролога." if r.get("needs_review") else ".")
    ),
    "si_type_create": lambda r: (
        f"Тип СИ уже был в справочнике (id {r['id']})." if r.get("existing") else
        f"Тип СИ заведён (id {r['id']}): {r['label']}."
        + (" Ждёт проверки метролога — протокол с ним не подпишется до проверки." if r.get("needs_review") else "")
    ),
    "memory_company_save": lambda r: f"Сохранено в общую память компании (id {r['id']}).",
    "memory_company_delete": lambda r: "Удалено из общей памяти." if r.get("deleted") else "Записи уже не было.",
}


def _registry() -> tuple[dict, dict]:
    """Свои исполнители + личный кабинет (график, наряды, задачи, заявки) — cabinet импортирует этот модуль."""
    from apps.assistant import cabinet

    return {**EXECUTORS, **cabinet.EXECUTORS}, {**DONE_TEXT, **cabinet.DONE_TEXT}


def decide(action: PendingAction, user, *, confirm: bool) -> PendingAction:
    if action.status != PendingAction.Status.PENDING:
        raise ActionError("Решение по этому действию уже принято")
    if action.conversation.user_id != user.pk:
        raise PermissionDenied("Это действие из чужого чата")
    action.decided_by = user
    action.decided_at = timezone.now()
    if not confirm:
        action.status = PendingAction.Status.REJECTED
        action.save()
        note = f"[Событие] Человек ОТКЛОНИЛ действие «{action.title}»."
    else:
        executors, done_text = _registry()
        executor = executors.get(action.kind)
        if executor is None:
            raise ActionError(f"Неизвестное действие {action.kind}")
        try:
            action.result = executor(action, user)
            action.status = PendingAction.Status.DONE
            note = f"[Событие] Человек подтвердил «{action.title}». {done_text[action.kind](action.result)}"
        except (ActionError, PermissionDenied) as exc:
            action.status = PendingAction.Status.FAILED
            action.error = str(getattr(exc, "detail", exc))
            note = f"[Событие] Подтверждено, но не выполнено «{action.title}»: {action.error}"
        action.save()
    Message.objects.create(
        conversation=action.conversation, role=Message.Role.EVENT, content=note,
        ui={"action": action.pk},
    )
    return action
