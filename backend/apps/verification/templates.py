"""Поверка СИ по шаблону: шаблон → поверитель вносит данные → расчёт, годность, протокол.

Счётчики воды считает свой калькулятор (``calculators.water_meter``) и печатает свой
Typst-шаблон. Для остальных семейств (манометры, весы, гири, термометры…) жёсткого кода
нет: что измерять и как считать задаёт **шаблон поверки** (``VerificationTemplate.plan``),
который ассистент собирает по методике поверки, а метролог проверяет.

Шаблон — это план аттестации (``apps.equipment.calc.clean_plan``: точки, режимы,
характеристики с формулами, допуски, каркас протокола) плюс то, что есть только у поверки:

* ``params`` — параметры прибора, от которых зависят поверяемые точки и допуски: верхний
  предел, класс точности, цена деления, Max весов… Значение берётся из характеристик типа
  СИ (``SiType.limits[from_type]``), иначе — из карточки экземпляра, иначе его вносит
  поверитель (и оно запоминается в ``Instrument.attributes``);
* ``modes[].setpoint`` и ``modes[].params`` — число **или выражение от параметров**
  («upper * 0.25», «cls / 100 * upper»);
* ``operations`` — операции поверки с отметкой «соответствует / нет» (внешний осмотр,
  опробование, проверка ПО…).

В терминах поверки: режим — поверяемая точка (отметка шкалы, нагрузка), точка плана —
измеряемая величина в этой отметке (прямой/обратный ход, показание эталона, показание СИ),
repeats — сколько отсчётов на величину.

Измерения в шаблоне не хранятся и прийти от ассистента не могут — только от человека.
"""

from __future__ import annotations

import copy
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.equipment import calc, expr, layout
from apps.verification.models import (
    Verification,
    VerificationStatus,
    VerificationTemplate,
)

ENGINE = "template"
KINDS = ("periodic", "primary")
DEFAULT_OPERATIONS = [
    {"key": "visual", "label": "Внешний осмотр", "source": ""},
    {"key": "testing", "label": "Опробование", "source": ""},
]
# Ключи плана, которые передаются движку как есть.
PLAN_KEYS = (
    "documents", "quantity", "unit", "checked", "repeats", "points", "characteristics", "conditions",
    "procedure", "purpose", "periodicity", "document", "conclusion", "layout",
)
FORBIDDEN = {"measurements", "readings", "results", "values", "equipment", "instrument", "serial_number"}


class TemplateError(ValueError):
    """Шаблон не прошёл проверку — сообщение для человека и ассистента."""


class MeasurementInputError(ValueError):
    """Ошибка ввода поверителя — показывается на экране, а не падает пятисоткой."""


# ---------------------------------------------------------------------------
# Шаблон
# ---------------------------------------------------------------------------
def _text(value, what: str, *, required: bool = True, limit: int = 300) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise TemplateError(f"не заполнено: {what}")
    return text[:limit]


def _expr_or_number(raw, what: str, allowed: set[str]) -> str:
    if raw is None or str(raw).strip() == "":
        raise TemplateError(f"{what}: не указано")
    if calc.is_number(raw):
        return str(calc.number(raw))
    source = str(raw).strip()
    try:
        unknown = expr.names(source) - allowed
    except expr.ExprError as exc:
        raise TemplateError(f"{what}: {exc}") from exc
    if unknown:
        raise TemplateError(
            f"{what}: неизвестные имена {', '.join(sorted(unknown))} — можно числа и параметры прибора "
            f"({', '.join(sorted(allowed)) or 'их нет'})"
        )
    return expr.normalize(source)


def clean_template(raw) -> dict:
    """Проверить шаблон и привести к каноническому виду.

    Проверка — прогон движком: шаблон разрешается на пробных значениях параметров
    (``sample``) и проходит ту же проверку, что план аттестации (формулы считаются,
    допуски с источниками, подстановки каркаса существуют).
    """
    if not isinstance(raw, dict):
        raise TemplateError("шаблон — объект")
    forbidden = FORBIDDEN & set(raw)
    if forbidden:
        raise TemplateError(
            f"в шаблоне не может быть {', '.join(sorted(forbidden))}: конкретный прибор и измерения вносит "
            "поверитель, когда проводит поверку по шаблону"
        )

    params, seen = [], set()
    for i, p in enumerate(raw.get("params") or []):
        if not isinstance(p, dict):
            raise TemplateError(f"params[{i}] — не объект")
        try:
            key = calc._ident(p.get("key"), f"params[{i}].key")
        except calc.PlanError as exc:
            raise TemplateError(str(exc)) from exc
        if key in seen:
            raise TemplateError(f"параметр «{key}» указан дважды")
        seen.add(key)
        sample = p.get("sample", 1)
        if not calc.is_number(sample):
            raise TemplateError(f"params[{i}].sample — пробное число для проверки формул")
        params.append({
            "key": key, "label": _text(p.get("label"), f"подпись параметра «{key}»", limit=120),
            "unit": _text(p.get("unit"), "единица", required=False, limit=20),
            "from_type": _text(p.get("from_type"), "ключ характеристики типа", required=False, limit=40),
            "sample": str(calc.number(sample)),
            "source": _text(p.get("source"), "источник", required=False),
        })
    if len(params) > 20:
        raise TemplateError("больше 20 параметров прибора — проверьте шаблон")
    names = {p["key"] for p in params}

    modes = []
    for i, m in enumerate(raw.get("modes") or []):
        if not isinstance(m, dict):
            raise TemplateError(f"modes[{i}] — не объект")
        mode = {
            "key": m.get("key") or f"m{i + 1}",
            "label": _text(m.get("label"), "подпись", required=False, limit=120),
            "setpoint": _expr_or_number(m.get("setpoint"), f"modes[{i}].setpoint (поверяемая точка)", names),
            "params": {
                str(k): _expr_or_number(v, f"modes[{i}].params.{k}", names) for k, v in (m.get("params") or {}).items()
            },
        }
        clash = names & set(mode["params"])
        if clash:
            raise TemplateError(f"modes[{i}].params: {', '.join(clash)} — это уже параметры прибора")
        modes.append(mode)
    if not modes:
        raise TemplateError("нет поверяемых точек (modes)")

    operations, seen_ops = [], set()
    for i, op in enumerate(raw.get("operations") or DEFAULT_OPERATIONS):
        if not isinstance(op, dict):
            raise TemplateError(f"operations[{i}] — не объект")
        key = _text(op.get("key") or f"op{i + 1}", f"operations[{i}].key", limit=20)
        if key in seen_ops:
            raise TemplateError(f"операция «{key}» указана дважды")
        seen_ops.add(key)
        operations.append({
            "key": key, "label": _text(op.get("label"), f"operations[{i}].label", limit=200),
            "source": _text(op.get("source"), "источник", required=False),
        })

    template = {
        "si_kind": _text(raw.get("si_kind"), "вид СИ (si_kind)", limit=200),
        "method": _text(raw.get("method"), "методика поверки (method)", limit=400),
        "params": params, "operations": operations, "modes": modes,
        **{k: copy.deepcopy(raw[k]) for k in PLAN_KEYS if k in raw},
    }
    resolve(template, {p["key"]: Decimal(p["sample"]) for p in params})
    return template


def _value(raw: str, env: dict) -> Decimal:
    return calc.number(raw) if calc.is_number(raw) else expr.evaluate_number(raw, env)


def resolve(template: dict, values: dict) -> dict:
    """Шаблон + значения параметров прибора → готовый план для движка расчёта и протокола."""
    env = {k: Decimal(str(v)) for k, v in values.items()}
    missing = [p["label"] for p in template["params"] if p["key"] not in env]
    if missing:
        raise TemplateError(f"не заданы параметры прибора: {', '.join(missing)}")
    modes = []
    for m in template["modes"]:
        try:
            setpoint = _value(m["setpoint"], env)
            mode_params = {k: str(_value(v, env)) for k, v in m["params"].items()}
        except expr.ExprError as exc:
            raise TemplateError(f"поверяемая точка {m.get('label') or m['key']}: {exc}") from exc
        label = m["label"].replace("{setpoint}", calc.fmt(setpoint, max(calc.places(setpoint), 0)))
        label = label.replace("{unit}", str(template.get("unit") or ""))
        modes.append({
            "key": m["key"], "setpoint": str(setpoint), "label": label or None,
            "params": {**{k: str(v) for k, v in env.items()}, **mode_params},
        })
    raw = {k: copy.deepcopy(template[k]) for k in PLAN_KEYS if k in template}
    raw.update(
        equipment_kind=template["si_kind"], program=template["method"], modes=modes, flavor=layout.VERIFICATION,
    )
    for m in raw["modes"]:
        if not m["label"]:
            m.pop("label")  # движок подпишет точку её значением
    try:
        return calc.clean_plan(raw)
    except calc.PlanError as exc:
        raise TemplateError(str(exc)) from exc


def template_for(si_type) -> VerificationTemplate | None:
    """Свой шаблон типа, иначе общий шаблон семейства."""
    own = VerificationTemplate.objects.filter(is_active=True, si_type=si_type).order_by("-updated_at").first()
    return own or (
        VerificationTemplate.objects.filter(is_active=True, family_id=si_type.family_id, si_type__isnull=True)
        .order_by("-updated_at").first()
    )


def uses_template(verification: Verification) -> bool:
    """По шаблону — всё, кроме счётчиков воды (у них свой калькулятор), если шаблон для типа есть."""
    from apps.catalog.api_types import WATER_CALCULATOR

    si_type = verification.instrument.si_type
    return si_type.family.calculator_key != WATER_CALCULATOR and template_for(si_type) is not None


def known_params(template: dict, instrument, given: dict | None = None) -> tuple[dict, list[dict]]:
    """Значения параметров прибора и чего не хватает.

    Порядок: характеристика типа (``from_type``) — она общая для всех экземпляров и проверена
    метрологом; затем внесённое поверителем сейчас; затем запомненное у экземпляра.
    """
    limits = instrument.si_type.limits or {}
    attrs = instrument.attributes or {}
    given = given or {}
    values, missing = {}, []
    for p in template["params"]:
        raw = None
        if p["from_type"] and limits.get(p["from_type"]) not in (None, ""):
            raw = limits[p["from_type"]]
        elif str(given.get(p["key"], "")).strip():
            raw = given[p["key"]]
        elif str(attrs.get(p["key"], "")).strip():
            raw = attrs[p["key"]]
        if raw is None:
            missing.append(p)
            continue
        try:
            values[p["key"]] = calc.number(raw, what=p["label"])
        except calc.PlanError as exc:
            raise MeasurementInputError(str(exc)) from exc
    return values, missing


# ---------------------------------------------------------------------------
# Измерения
# ---------------------------------------------------------------------------
def _template(verification: Verification, template_id) -> VerificationTemplate:
    si_type = verification.instrument.si_type
    if template_id:
        template = VerificationTemplate.objects.filter(pk=template_id, is_active=True).first()
        if template is None:
            raise MeasurementInputError(f"Шаблона поверки {template_id} нет или он выключен")
        if template.family_id != si_type.family_id or (template.si_type_id and template.si_type_id != si_type.pk):
            raise MeasurementInputError(f"Шаблон «{template}» не для типа «{si_type}»")
        return template
    template = template_for(si_type)
    if template is None:
        raise MeasurementInputError(
            f"Для «{si_type}» нет шаблона поверки. Попросите ассистента составить шаблон по методике поверки "
            "(или метролога — завести его)."
        )
    return template


def _kit(template: VerificationTemplate, verification: Verification, kit_id) -> dict | None:
    """Строка «Перечня применяемых эталонов», выбранная поверителем, — снимком в поверку.

    Не выбрал, а набор у шаблона один — берём его; наборов нет — поверка без набора (как раньше).
    """
    from apps.catalog import standard_kits

    options = standard_kits.kits_for(verification.instrument.si_type, template)
    if kit_id:
        chosen = next((k for k in options if str(k.pk) == str(kit_id)), None)
        if chosen is None:
            raise MeasurementInputError("Этот набор эталонов не для этого шаблона — выберите из списка")
        return standard_kits.snapshot(chosen)
    if len(options) == 1:
        return standard_kits.snapshot(options[0])
    if len(options) > 1:
        raise MeasurementInputError("Выберите набор эталонов — какой брали на эту поверку")
    return None


@transaction.atomic
def apply(verification: Verification, payload: dict) -> dict:
    """Записать данные поверителя по шаблону и посчитать результат.

    payload: {template_id?, kind?, params{ключ: число}, operations{ключ: bool}, readings{режим: {readings:
    {величина: [отсчёты]}, display?}}, conditions?{ключ: число}, manual_unsuitable?, manual_unsuitability_reason?}
    """
    if hasattr(verification, "protocol") and verification.protocol.is_sealed:
        raise MeasurementInputError(
            f"Протокол {verification.protocol.full_number} уже подписан — измерения меняются только выпуском новой версии"
        )
    payload = payload if isinstance(payload, dict) else {}
    template = _template(verification, payload.get("template_id"))
    instrument = verification.instrument
    given = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    values, missing = known_params(template.plan, instrument, given)
    if missing:
        raise MeasurementInputError("Не заполнены параметры прибора: " + ", ".join(p["label"] for p in missing))
    try:
        plan = resolve(template.plan, values)
    except TemplateError as exc:
        raise MeasurementInputError(f"Шаблон «{template}»: {exc}") from exc

    kind = payload.get("kind") or "periodic"
    if kind not in KINDS:
        raise MeasurementInputError("вид поверки — periodic | primary")
    kit = _kit(template, verification, payload.get("kit"))
    manual = bool(payload.get("manual_unsuitable"))
    manual_reason = str(payload.get("manual_unsuitability_reason") or "").strip()
    if manual and not manual_reason:
        raise MeasurementInputError("Укажите причину непригодности")

    raw_ops = payload.get("operations") if isinstance(payload.get("operations"), dict) else {}
    operations, not_marked = [], []
    for op in template.plan["operations"]:
        value = raw_ops.get(op["key"])
        if value is None:
            not_marked.append(op["label"])
        operations.append({"key": op["key"], "label": op["label"], "passed": None if value is None else bool(value)})
    if not_marked and not manual:
        raise MeasurementInputError("Отметьте операции поверки: " + ", ".join(not_marked))

    readings = payload.get("readings") if isinstance(payload.get("readings"), dict) else {}
    try:
        clean, missing_cells = calc.clean_measurements(plan, readings)
    except calc.PlanError as exc:
        raise MeasurementInputError(str(exc)) from exc
    empty = all(not any(v for vs in m["readings"].values() for v in vs) for m in clean.values())
    computed = None
    if missing_cells and not (manual and empty):
        raise MeasurementInputError("Не хватает показаний: " + "; ".join(missing_cells[:6]))
    if not missing_cells:
        try:
            computed = calc.compute(plan, clean)
        except (calc.PlanError, expr.ExprError) as exc:
            raise MeasurementInputError(f"Расчёт по шаблону не удался: {exc}") from exc

    reasons = [f"{op['label']} — не соответствует" for op in operations if op["passed"] is False]
    for m in (computed or {}).get("modes", []):
        for r in m["characteristics"]:
            if r["passed"] is False:
                where = f", {r['point_label']}" if r["point"] else ""
                reasons.append(f"{r['label']}{where} ({m['label']}): {r['shown']} при допуске {r['tolerance']}")
    if manual:
        reasons.append(manual_reason)
    suitable = not reasons and (computed is None or computed["suitable"])

    conditions = {}
    for key, value in (payload.get("conditions") or {}).items():
        if str(value).strip():
            try:
                conditions[str(key)] = str(calc.number(value, what=f"условие {key}"))
            except calc.PlanError as exc:
                raise MeasurementInputError(str(exc)) from exc

    remember = {
        p["key"]: str(values[p["key"]]) for p in template.plan["params"]
        if not (p["from_type"] and (instrument.si_type.limits or {}).get(p["from_type"]) not in (None, ""))
    }
    if remember and any((instrument.attributes or {}).get(k) != v for k, v in remember.items()):
        instrument.attributes = {**(instrument.attributes or {}), **remember}
        instrument.save(update_fields=["attributes"])

    verification.measurements = {
        "engine": ENGINE,
        "template": {"id": template.pk, "title": template.title, "updated_at": template.updated_at.isoformat()},
        "kind": kind, "params": {k: str(v) for k, v in values.items()}, "plan": plan,
        "operations": operations, "readings": clean, "conditions": conditions,
        "manual_unsuitable": manual, "manual_unsuitability_reason": manual_reason,
        "kit": kit, "source": payload.get("source") or "manual",
    }
    verification.results = {
        "engine": ENGINE, "modes": (computed or {}).get("modes", []), "suitable": suitable,
        "reasons": reasons, "failed_rows": [], "journal_note": "",
    }
    verification.suitable = suitable
    verification.unsuitability_reason = "; ".join(reasons) if not suitable else ""
    verification.needs_review = False
    if verification.status in (VerificationStatus.DRAFT, VerificationStatus.REJECTED):
        verification.status = VerificationStatus.READY
    verification.save(update_fields=[
        "measurements", "results", "suitable", "unsuitability_reason", "status", "needs_review", "updated_at",
    ])
    return {
        "suitable": suitable, "rows": verification.results["modes"], "failed_rows": [], "reasons": reasons,
        "journal_note": "", "needs_review": [], "status": verification.status, "engine": ENGINE,
    }


# ---------------------------------------------------------------------------
# Протокол
# ---------------------------------------------------------------------------
def _equipment(instrument) -> dict:
    si_type = instrument.si_type
    attrs = instrument.attributes or {}
    return {
        "name": si_type.name, "registry_number": si_type.registry_number,
        "modification": str(attrs.get("modification") or ""), "serial_number": instrument.serial_number,
        "year": str(instrument.manufacture_year or ""), "manufacturer": si_type.manufacturer,
        "owner": str(instrument.owner) if instrument.owner_id else "Частное лицо",
        "location": str(instrument.site) if instrument.site_id else "",
    }


def _standards(verification: Verification | None) -> list[dict]:
    if verification is None or verification.pk is None:
        return []
    rows = [
        {"name": s.name, "serial_number": s.serial_number, "characteristics": "",
         "verification": f"рег. № в ФИФ {s.fif_number}", "valid_until": s.valid_until.isoformat()}
        for s in verification.standards.all()
    ]
    kit = (verification.measurements or {}).get("kit") or {}
    if not rows and kit:
        # Набор из перечня компании: эталоны — со своими номерами в ФИФ ОЕИ, затем вспомогательные СИ.
        numbers = ", ".join(kit.get("fif_numbers") or [])
        for line in str(kit.get("standards_text") or "").split("\n"):
            if line.strip():
                rows.append({"name": line.strip().rstrip(";"), "serial_number": "", "characteristics": "",
                             "verification": f"рег. № в ФИФ {numbers}" if numbers else "", "valid_until": ""})
                numbers = ""
        for line in str(kit.get("aux_text") or "").split("\n"):
            if line.strip():
                rows.append({"name": line.strip().rstrip(";"), "serial_number": "", "characteristics": "",
                             "verification": "", "valid_until": ""})
    return rows


def protocol_spec(verification: Verification, *, draft: bool | None = None) -> dict:
    """Протокол поверки по шаблону — блоками apps.assistant.documents (Word и PDF из одного источника)."""
    m = verification.measurements or {}
    if m.get("engine") != ENGINE or not m.get("plan"):
        raise MeasurementInputError("Поверка ещё не заполнена по шаблону")
    protocol = getattr(verification, "protocol", None)
    number = protocol.full_number if protocol and protocol.seq is not None else None
    if draft is None:
        draft = not (protocol and protocol.is_sealed)
    conditions = {
        "temperature": verification.temperature, "humidity": verification.humidity,
        "pressure": verification.pressure, **(m.get("conditions") or {}),
    }
    verifier = verification.verifier
    on = timezone.localtime(verification.verified_at).date()
    # В «Причины» заключения — непрошедшие операции и характеристики (их видит каркас) + ручная причина.
    manual = [m["manual_unsuitability_reason"]] if m.get("manual_unsuitable") and m.get("manual_unsuitability_reason") else []
    results = {"modes": [], **(verification.results or {}), "suitable": verification.suitable, "extra_reasons": manual}
    ctx = layout.Context(
        plan=m["plan"], equipment=_equipment(verification.instrument), commission=[],
        instruments=_standards(verification), texts={},
        conditions={k: str(v) for k, v in conditions.items() if v not in (None, "")},
        measurements=m.get("readings") or {}, results=results,
        kind=m.get("kind") or "periodic", number=number, protocol_date=on, performed_on=on,
        operations=m.get("operations"),
        values={"verifier": verifier.short_name or verifier.full_name, "method": m["plan"]["program"]},
    )
    return layout.build(ctx, draft=draft)


def preview_spec(template: dict, *, title: str = "") -> dict:
    """Пустой протокол по шаблону — как его увидит человек, до поверки (пробные значения параметров)."""
    plan = resolve(template, {p["key"]: Decimal(p["sample"]) for p in template["params"]})
    blank = layout.BLANK
    ctx = layout.Context(
        plan=plan, equipment={"name": blank, "serial_number": blank, "registry_number": blank},
        commission=[], instruments=[], texts={}, conditions={}, measurements={}, results=None,
        kind="periodic", number=None, protocol_date=None, performed_on=None,
        operations=[{**op, "passed": None} for op in template["operations"]],
        values={"verifier": blank, "method": plan["program"]},
    )
    spec = layout.build(ctx, draft=True)
    spec["footer"] = "Шаблон протокола" + (f" «{title}»" if title else "") + \
        " — поверочные точки рассчитаны на пробных значениях параметров прибора"
    return spec


def render(spec: dict, fmt: str) -> bytes:
    from apps.assistant import documents

    return documents.render_docx(spec) if fmt == "docx" else documents.render_pdf(spec)


# ---------------------------------------------------------------------------
# Заведение шаблона (экран и карточка ассистента)
# ---------------------------------------------------------------------------
def save_template(user, data: dict, *, sources=None, plan_check=None, template_id=None,
                  via_assistant: bool = False, force_review: bool = False) -> VerificationTemplate:
    """Сохранить шаблон. ``force_review`` — черновик от агента: ждёт метролога, даже если запустил метролог."""
    from rest_framework.exceptions import PermissionDenied

    from apps.catalog.api_types import can_create, is_reviewer
    from apps.catalog.models import MeasurementFamily, SiType

    if not can_create(user):
        raise PermissionDenied("Нет права заводить шаблоны поверки (наблюдатель)")
    plan = clean_template(data.get("plan"))
    title = _text(data.get("title"), "название шаблона (title)", limit=250)
    family = MeasurementFamily.objects.filter(pk=data.get("family")).first()
    if family is None:
        raise TemplateError("не указано семейство СИ (family — id из справочника семейств)")
    si_type = None
    if data.get("si_type"):
        si_type = SiType.objects.filter(pk=data["si_type"]).first()
        if si_type is None:
            raise TemplateError(f"типа СИ {data['si_type']} нет в справочнике")
        if si_type.family_id != family.pk:
            raise TemplateError(f"тип «{si_type}» из другого семейства")
    from apps.catalog.models import StandardKit

    kit_ids = [int(k) for k in (data.get("kits") or []) if str(k).strip().isdigit()]
    kits = list(StandardKit.objects.filter(pk__in=kit_ids, is_active=True))
    if len(kits) != len(set(kit_ids)):
        raise TemplateError("kits — id строк «Перечня применяемых эталонов» (ei_standard_kits), только действующие")
    reviewer = is_reviewer(user) and not force_review
    with transaction.atomic():
        if template_id:
            template = VerificationTemplate.objects.select_for_update().filter(pk=template_id).first()
            if template is None:
                raise TemplateError(f"шаблона поверки {template_id} нет")
        else:
            if VerificationTemplate.objects.filter(title=title).exists():
                raise TemplateError(f"шаблон «{title}» уже есть — обновите его или назовите иначе")
            template = VerificationTemplate(created_by=user)
        template.title, template.family, template.si_type, template.plan = title, family, si_type, plan
        template.note = _text(data.get("note"), "примечание", required=False, limit=2000)
        template.sources = list(sources or [])
        template.plan_check = plan_check or {}
        template.created_via_assistant = via_assistant
        template.needs_review = not reviewer
        template.reviewed_by = user if reviewer else None
        template.reviewed_at = timezone.now() if reviewer else None
        template.is_active = True
        template.save()
        if "kits" in data:
            template.kits.set(kits)
    return template


def approve(template: VerificationTemplate, user) -> VerificationTemplate:
    from rest_framework.exceptions import PermissionDenied

    from apps.catalog.api_types import is_reviewer

    if not is_reviewer(user):
        raise PermissionDenied("Шаблон поверки проверяет метролог или руководитель")
    template.needs_review, template.reviewed_by, template.reviewed_at = False, user, timezone.now()
    template.save(update_fields=["needs_review", "reviewed_by", "reviewed_at", "updated_at"])
    return template


def check_example(template: dict, example: dict) -> dict:
    """Сверка с примером протокола: params примера → план → calc.check_example."""
    if not isinstance(example, dict):
        raise TemplateError("example — объект {params, modes, source}")
    given = example.get("params") or {}
    values = {p["key"]: calc.number(given.get(p["key"], p["sample"])) for p in template["params"]}
    plan = resolve(template, values)
    try:
        return calc.check_example(plan, example)
    except calc.PlanError as exc:
        raise TemplateError(f"сверка не выполнена: {exc}") from exc


def template_help() -> str:
    """Формат шаблона поверки для ассистента (в описание инструментов)."""
    return (
        "plan{si_kind (вид СИ: «Манометры показывающие»), method (методика поверки — обозначение и название, как "
        "в карточке типа ФГИС), documents[] (прочие НД), quantity, unit, "
        "params[{key (латиница), label, unit, from_type? (ключ характеристики типа СИ в справочнике EI_doc, "
        "SiType.limits — тогда значение берётся из типа), sample (пробное число для проверки формул), source}] — "
        "параметры прибора, от которых зависят точки и допуски (верхний предел, класс точности, Max, цена "
        "деления); если from_type нет — значение вносит поверитель; "
        "operations[{key, label, source}] — операции с отметкой «соответствует» (внешний осмотр, опробование, "
        "проверка ПО, герметичность) — по методике; "
        "points[{key, label}] — что записывается в каждой поверяемой точке (прямой/обратный ход, показание "
        "эталона, показание СИ; для весов — показание при нагружении/разгружении); repeats — отсчётов на каждое; "
        "modes[{key, label («{setpoint} {unit}» — подставится число), setpoint — число или выражение от params "
        "(«upper * 0.25»), params?{имя: число|выражение}}] — поверяемые точки (отметки шкалы, нагрузки); "
        "characteristics[…] — как в плане аттестации: expr от setpoint, params, отсчётов (point(\"up\") — отсчёты "
        "величины up, mean(...)), tolerance с границами-выражениями от setpoint и params («cls / 100 * upper»), "
        "source — пункт методики/описания типа обязателен; conditions?, procedure?, document?, conclusion? "
        "(подстановки {{who}}, {{failed}}, {{kind}}), layout? (блоки как у аттестации + operations — операции с "
        "отметками, grid{points?, characteristics?, tolerance?, verdict?, titles?} — таблица «точка × величины»; "
        "простые подстановки {{verifier}}, {{method}}, equipment.name|registry_number|modification|serial_number|"
        "year|manufacturer|owner|location)}. Без layout — стандартный протокол поверки EI_doc. "
        "Подробности формул: " + calc.plan_help()
    )
