"""Операции над аттестацией: проверка карточки, измерения, подпись, протокол.

Всё, что меняет данные, проходит через эти функции — и экран, и ассистент
(через кнопку подтверждения), поэтому правила одни для всех.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.equipment import calc, layout
from apps.equipment.models import AttestationCounter, EquipmentAttestation

KIND_GENITIVE = {k: v[0] for k, v in layout.KIND.items()}
COMMISSION_ROLES = layout.COMMISSION_ROLES
TEXT_KEYS = layout.TEXT_KEYS


class AttestationError(ValueError):
    """Нарушено правило — сообщение для человека."""


def _roles():
    from apps.catalog.api_types import can_create, is_reviewer

    return can_create, is_reviewer


def can_edit(user) -> bool:
    return _roles()[0](user)


def is_reviewer(user) -> bool:
    return _roles()[1](user)


# ---------------------------------------------------------------------------
# Карточка
# ---------------------------------------------------------------------------
def _s(value, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _date(value, what: str) -> str:
    if value in (None, ""):
        return ""
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as exc:
        raise AttestationError(f"{what}: дата в формате ГГГГ-ММ-ДД") from exc


def clean_equipment(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {k: _s(raw.get(k)) for k in (
        "name", "serial_number", "inventory_number", "manufacturer", "year", "owner", "location",
    )}
    if not out["name"]:
        raise AttestationError("не указано наименование оборудования")
    if not out["serial_number"]:
        raise AttestationError("не указан заводской номер оборудования")
    return out


def clean_commission(raw) -> list[dict]:
    rows = []
    for i, r in enumerate(raw or []):
        if not isinstance(r, dict):
            raise AttestationError(f"комиссия, строка {i + 1}: не объект")
        role = r.get("role", "member")
        if role not in COMMISSION_ROLES:
            raise AttestationError("роль в комиссии — chair (председатель) или member (член)")
        name = _s(r.get("name"), 120)
        if not name:
            raise AttestationError(f"комиссия, строка {i + 1}: нет ФИО")
        rows.append({"role": role, "name": name, "position": _s(r.get("position"), 400)})
    if sum(r["role"] == "chair" for r in rows) > 1:
        raise AttestationError("председатель комиссии может быть только один")
    rows.sort(key=lambda r: r["role"] != "chair")
    return rows


def clean_instruments(raw) -> list[dict]:
    rows = []
    for i, r in enumerate(raw or []):
        if not isinstance(r, dict):
            raise AttestationError(f"средства измерений, строка {i + 1}: не объект")
        name = _s(r.get("name"))
        if not name:
            raise AttestationError(f"средства измерений, строка {i + 1}: нет наименования")
        rows.append({
            "name": name, "serial_number": _s(r.get("serial_number"), 60),
            "registry_number": _s(r.get("registry_number"), 40),
            "characteristics": _s(r.get("characteristics"), 600),
            "verification": _s(r.get("verification"), 400),
            "valid_until": _date(r.get("valid_until"), f"«{name}»: годен до"),
        })
    return rows


def clean_texts(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {k: _s(raw.get(k), 4000) for k in TEXT_KEYS if _s(raw.get(k))}


def clean_conditions(plan: dict, raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for cond in plan.get("conditions") or calc.DEFAULT_CONDITIONS:
        key = cond["key"]
        if str(raw.get(key, "")).strip():
            try:
                out[key] = str(calc.number(raw[key], what=cond["label"]))
            except calc.PlanError as exc:
                raise AttestationError(str(exc)) from exc
    return out


def clean_card(data: dict) -> dict:
    """Проверить всё, кроме измерений. Используется экраном и ассистентом."""
    try:
        plan = calc.clean_plan(data.get("plan"))
    except calc.PlanError as exc:
        raise AttestationError(f"план аттестации: {exc}") from exc
    kind = data.get("kind") or "periodic"
    if kind not in KIND_GENITIVE:
        raise AttestationError("вид аттестации — primary | periodic | repeat")
    return {
        "kind": kind,
        "equipment": clean_equipment(data.get("equipment")),
        "commission": clean_commission(data.get("commission")),
        "instruments": clean_instruments(data.get("instruments")),
        "plan": plan,
        "texts": clean_texts(data.get("texts")),
    }


# ---------------------------------------------------------------------------
# Изменения
# ---------------------------------------------------------------------------
def _editable(att: EquipmentAttestation, user) -> None:
    if not can_edit(user):
        raise PermissionDenied("Нет права менять аттестации (наблюдатель)")
    if att.is_signed:
        raise AttestationError("протокол подписан — изменить нельзя")


def create(user, data: dict, *, via_assistant: bool = False, sources=None, program=None,
           plan_check: dict | None = None) -> EquipmentAttestation:
    if not can_edit(user):
        raise PermissionDenied("Нет права заводить аттестации (наблюдатель)")
    card = clean_card(data)
    att = EquipmentAttestation(
        **card, program=program, created_by=user, created_via_assistant=via_assistant, sources=sources or [],
        plan_check=plan_check or {},
    )
    # Метролог, заводящий карточку сам (или подтверждающий кнопкой в чате), этим и проверяет план.
    if is_reviewer(user):
        att.plan_approved_by, att.plan_approved_at = user, timezone.now()
    att.save()
    return att


def update(att: EquipmentAttestation, user, data: dict) -> EquipmentAttestation:
    _editable(att, user)
    merged = {
        "kind": data.get("kind", att.kind), "equipment": data.get("equipment", att.equipment),
        "commission": data.get("commission", att.commission), "instruments": data.get("instruments", att.instruments),
        "plan": data.get("plan", att.plan), "texts": data.get("texts", att.texts),
    }
    card = clean_card(merged)
    if card["plan"] != att.plan:
        if att.measurements:
            try:
                calc.clean_measurements(card["plan"], att.measurements)
            except calc.PlanError as exc:
                raise AttestationError(
                    f"новый план не совпадает с уже внесёнными измерениями ({exc}) — сначала очистите измерения"
                ) from exc
        att.plan_approved_by = att.plan_approved_at = None
        if att.plan_check:
            att.plan_check = {**att.plan_check, "stale": True}  # сверка была для прежнего плана
        if is_reviewer(user):
            att.plan_approved_by, att.plan_approved_at = user, timezone.now()
    for key, value in card.items():
        setattr(att, key, value)
    att.save()
    return att


def approve_plan(att: EquipmentAttestation, user) -> EquipmentAttestation:
    if not is_reviewer(user):
        raise PermissionDenied("План аттестации проверяет метролог или руководитель")
    _editable(att, user)
    att.plan_approved_by, att.plan_approved_at = user, timezone.now()
    att.save(update_fields=["plan_approved_by", "plan_approved_at", "updated_at"])
    return att


def save_measurements(att: EquipmentAttestation, user, data: dict) -> tuple[EquipmentAttestation, list[str]]:
    """Внести измерения. Только человек с экрана — ассистенту эта функция не доступна."""
    _editable(att, user)
    try:
        measurements, missing = calc.clean_measurements(att.plan, data.get("measurements"))
    except calc.PlanError as exc:
        raise AttestationError(str(exc)) from exc
    att.measurements = measurements
    att.conditions = clean_conditions(att.plan, data.get("conditions"))
    performed = _date(data.get("performed_on"), "дата проведения измерений")
    att.performed_on = dt.date.fromisoformat(performed) if performed else None
    if att.performed_on and att.performed_on > timezone.localdate():
        raise AttestationError("дата проведения измерений ещё не наступила")
    att.measurements_by, att.measurements_at = user, timezone.now()
    att.save()
    return att, missing


def preview(att: EquipmentAttestation) -> dict:
    """Расчёт по текущим данным для экрана: результат или чего не хватает."""
    if att.is_signed:
        return {"complete": True, "missing": [], "results": att.results}
    try:
        return {"complete": True, "missing": [], "results": calc.compute(att.plan, att.measurements)}
    except calc.Incomplete as exc:
        return {"complete": False, "missing": str(exc).split("; "), "results": None}
    except calc.PlanError as exc:
        return {"complete": False, "missing": [str(exc)], "results": None}


def sign_problems(att: EquipmentAttestation, protocol_date: dt.date) -> list[str]:
    problems = []
    if not att.plan_approved:
        problems.append("план аттестации не проверен метрологом")
    if not att.performed_on:
        problems.append("не указана дата проведения измерений")
    elif protocol_date < att.performed_on:
        problems.append("дата протокола раньше даты измерений")
    if protocol_date > timezone.localdate():
        problems.append("дата протокола ещё не наступила — протокол оформляется после измерений")
    missing = [c["label"] for c in att.plan.get("conditions") or calc.DEFAULT_CONDITIONS
               if c["key"] not in (att.conditions or {})]
    if missing:
        problems.append("не внесены условия проведения: " + ", ".join(missing))
    if not any(r["role"] == "chair" for r in att.commission):
        problems.append("не указан председатель комиссии")
    if not att.instruments:
        problems.append("не указаны средства измерений")
    on = att.performed_on or protocol_date
    for r in att.instruments:
        if not r.get("valid_until"):
            problems.append(f"«{r['name']}»: не указан срок действия поверки")
        elif dt.date.fromisoformat(r["valid_until"]) < on:
            problems.append(f"«{r['name']}»: поверка истекла {dt.date.fromisoformat(r['valid_until']):%d.%m.%Y}")
    calc_state = preview(att)
    if not calc_state["complete"]:
        problems.append("измерения не полные: " + "; ".join(calc_state["missing"][:5]))
    return problems


def sign(att: EquipmentAttestation, user, protocol_date: dt.date | None = None) -> EquipmentAttestation:
    if not is_reviewer(user):
        raise PermissionDenied("Протокол аттестации подписывает метролог или руководитель")
    protocol_date = protocol_date or timezone.localdate()
    with transaction.atomic():
        att = EquipmentAttestation.objects.select_for_update().get(pk=att.pk)
        _editable(att, user)
        problems = sign_problems(att, protocol_date)
        if problems:
            raise AttestationError("Подписать нельзя: " + "; ".join(problems))
        results = calc.compute(att.plan, att.measurements)
        counter, _ = AttestationCounter.objects.select_for_update().get_or_create(year=protocol_date.year)
        counter.last += 1
        counter.save(update_fields=["last"])
        att.number, att.year, att.protocol_date = counter.last, protocol_date.year, protocol_date
        att.results, att.suitable = results, results["suitable"]
        att.status = EquipmentAttestation.Status.SIGNED
        att.signed_by, att.signed_at = user, timezone.now()
        att.snapshot = document_spec(att)
        att.sha256 = att.digest(att.snapshot)
        att.save()
    return att


# ---------------------------------------------------------------------------
# Протокол
# ---------------------------------------------------------------------------
def document_spec(att: EquipmentAttestation) -> dict:
    """Протокол по каркасу из плана (apps.equipment.layout)."""
    if att.is_signed and att.snapshot:
        return att.snapshot
    ctx = layout.Context(
        plan=att.plan, equipment=att.equipment, commission=att.commission, instruments=att.instruments,
        texts=att.texts, conditions=att.conditions, measurements=att.measurements,
        results=preview(att)["results"], kind=att.kind, number=att.full_number or None,
        protocol_date=att.protocol_date, performed_on=att.performed_on,
    )
    return layout.build(ctx, draft=not att.is_signed)


def render(att: EquipmentAttestation, fmt: str) -> bytes:
    from apps.assistant import documents

    spec = document_spec(att)
    return documents.render_docx(spec) if fmt == "docx" else documents.render_pdf(spec)


def filename(att: EquipmentAttestation, ext: str) -> str:
    from apps.assistant import documents

    e = att.equipment
    stem = f"Протокол {'№' + att.full_number.replace('/', '-') if att.full_number else 'аттестации (черновик)'} {e.get('name', '')} №{e.get('serial_number', '')}"
    return documents.safe_filename(stem, ext)
