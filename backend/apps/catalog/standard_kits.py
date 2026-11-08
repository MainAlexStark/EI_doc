"""«Перечень применяемых эталонов» компании: импорт из Word, правка, выбор набора под прибор.

Документ живёт у метролога в Word и часто меняется, поэтому:

* ``import_docx`` разбирает его как есть — разделы (заголовки) и таблицы «Класс точности /
  диапазон · Наименование · СИ, применяемые при поверке · Обозначение для журнала (столбец U) ·
  Номера в ФИФ ОЕИ» — и обновляет справочник **на месте**: строка узнаётся по разделу и
  диапазону, поэтому ссылки шаблонов поверки на наборы не рвутся; исчезнувшие строки
  выключаются, а не удаляются;
* ``apply_changes`` — точечные правки (добавить / изменить / выключить) — для экрана
  «Эталоны» и для ассистента («у нас добавилось оборудование — обнови перечень»);
* ``kits_for`` — наборы для типа СИ: из шаблона поверки, иначе по семейству.
"""

from __future__ import annotations

import io
import re

from django.db import transaction
from django.db.models import Q

from apps.catalog.models import AuxInstrument, MeasurementFamily, StandardKit

FIF_NUMBER = re.compile(r"\d{2,6}\.\d{2}\.[0-9A-Za-zА-Яа-яЁё]{1,4}\.\d{5,}")
KIT_FIELDS = ("section", "scope", "standards_text", "aux_text", "journal_aux", "note")
AUX_FIELDS = ("name", "designation", "note")


class KitError(ValueError):
    """Ошибка правки перечня — текст для человека и ассистента."""


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in str(text or "").replace("\r", "").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower().replace("ё", "е"))


def fif_numbers(text) -> list[str]:
    if isinstance(text, (list, tuple)):
        text = "|".join(map(str, text))
    seen, out = set(), []
    for number in FIF_NUMBER.findall(str(text or "")):
        if number not in seen:
            seen.add(number)
            out.append(number)
    return out


def guess_families(text: str) -> list[MeasurementFamily]:
    """Семейства, о которых говорит текст: «…поверки манометров» → «Манометры».

    Осторожно: совпасть должны все значимые слова названия семейства (по основе), иначе
    «Счетчики газа» угадались бы как «Счётчики воды». Не угадали — человек выберет сам.
    """
    hay = _key(text)
    found = []
    for family in MeasurementFamily.objects.all():
        words = [w for w in re.findall(r"[а-яёa-z]+", family.name.lower().replace("ё", "е")) if len(w) >= 4]
        if words and all(w[:5] in hay for w in words):
            found.append(family)
    return found


def guess_family(text: str) -> MeasurementFamily | None:
    found = guess_families(text)
    return found[0] if len(found) == 1 else None


def _employee(section: str):
    from apps.core.models import Employee

    match = re.search(r"([А-ЯЁ][а-яё]+)\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s*$", section.strip())
    if not match:
        return None
    return Employee.objects.filter(full_name__istartswith=match.group(1)).first()


# ---------------------------------------------------------------------------
# Разбор Word
# ---------------------------------------------------------------------------
def _cells(row) -> list[str]:
    out, prev = [], None
    for cell in row.cells:
        if cell._tc is prev:  # объединённые ячейки python-docx отдаёт повторно
            continue
        prev = cell._tc
        out.append(_clean(cell.text))
    return out


def parse_docx(content: bytes) -> dict:
    """Word → {"aux": [{name, designation, note, is_active}], "kits": [{section, scope, …}]}."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 — python-docx кидает разное на битом файле
        raise KitError("Не получилось открыть файл — нужен документ Word (.docx)") from exc

    aux: list[dict] = []
    kits: list[dict] = []
    h1 = ""
    heading = ""
    note = ""
    since_table = True  # заголовок встретился после последней таблицы
    pending_h1 = ""

    for el in document.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, document)
            style = (p.style.name if p.style is not None else "") or ""
            text = _clean(p.text)
            if not text or not style.lower().startswith(("heading", "заголовок")):
                continue
            level = 1 if style.endswith("1") else 2
            if level == 1:
                h1, heading, note, pending_h1 = text, text, "", text
            else:
                if pending_h1 and since_table and h1 and not h1.upper() == h1:
                    # «Наборы до 3000 кг…» + «Уведомлять, если поверка более 2000 кг» — раздел и примечание.
                    heading, note = h1, text
                else:
                    heading, note = text, ""
            since_table = True
            continue
        if tag != "tbl":
            continue
        since_table = False
        pending_h1 = ""
        table = Table(el, document)
        rows = [_cells(r) for r in table.rows]
        if not rows:
            continue
        width = max(len(r) for r in rows)
        if width <= 3:  # «Для внесения в АРШИН СИ»: наименование ^ обозначение
            for cells in rows:
                name = cells[0] if cells else ""
                designation = cells[1] if len(cells) > 1 else ""
                if not name or name.lower().startswith("наименование"):
                    continue
                inactive = name.lower().startswith("пока что не поверен")
                if inactive:
                    name = _clean(re.sub(r"(?i)^пока что не поверены?", "", name))
                aux.append({
                    "name": name[:250], "designation": ("" if inactive else designation.replace("\n", "").strip().strip("|"))[:120],
                    "note": ("пока не поверены; номера в ФИФ: " + ", ".join(fif_numbers(designation)))[:250]
                    if inactive else "",
                    "is_active": not inactive,
                })
            continue
        for cells in rows:
            cells = (cells + [""] * 5)[:5]
            scope, standards, aux_text, journal, fif = cells
            if scope.lower().startswith("класс точности") or not any(cells):
                continue
            kits.append({
                "section": _clean(heading)[:250] or "Без раздела",
                "scope": scope, "standards_text": standards, "aux_text": aux_text,
                "journal_aux": journal, "fif_numbers": fif_numbers(fif) or fif_numbers(standards),
                "note": note,
            })
    if not kits and not aux:
        raise KitError("В документе не нашлось таблиц перечня эталонов")
    return {"aux": aux, "kits": kits}


@transaction.atomic
def import_docx(content: bytes, user=None) -> dict:
    """Обновить перечень по документу. Строки узнаются по (раздел, диапазон)."""
    parsed = parse_docx(content)
    created = updated = 0

    existing = {(_key(k.section), _key(k.scope)): k for k in StandardKit.objects.all()}
    seen: set[int] = set()
    for sort, row in enumerate(parsed["kits"], start=1):
        key = (_key(row["section"]), _key(row["scope"]))
        kit = existing.get(key)
        if kit is None or kit.pk in seen:
            kit = StandardKit(section=row["section"], scope=row["scope"])
            created += 1
            fresh = True
        else:
            updated += 1
            fresh = False
        for field in KIT_FIELDS:
            setattr(kit, field, row[field])
        kit.fif_numbers = row["fif_numbers"]
        kit.sort = sort
        kit.is_active = True
        kit.updated_by = user if getattr(user, "pk", None) else None
        if kit.employee_id is None:
            kit.employee = _employee(row["section"])
        kit.save()
        seen.add(kit.pk)
        if fresh or not kit.families.exists():
            kit.families.set(guess_families(f"{row['section']} {row['scope']}"))
    disabled = StandardKit.objects.filter(is_active=True).exclude(pk__in=seen).update(is_active=False)

    aux_existing = {_key(a.name): a for a in AuxInstrument.objects.all()}
    aux_seen: set[int] = set()
    for sort, row in enumerate(parsed["aux"], start=1):
        item = aux_existing.get(_key(row["name"])) or AuxInstrument(name=row["name"])
        for field in (*AUX_FIELDS, "is_active"):
            setattr(item, field, row[field])
        item.sort = sort
        item.save()
        aux_seen.add(item.pk)
    aux_disabled = AuxInstrument.objects.filter(is_active=True).exclude(pk__in=aux_seen).update(is_active=False)
    return {"kits_created": created, "kits_updated": updated, "kits_disabled": disabled,
            "aux": len(aux_seen), "aux_disabled": aux_disabled}


# ---------------------------------------------------------------------------
# Чтение и правка
# ---------------------------------------------------------------------------
def kit_dict(kit: StandardKit) -> dict:
    return {
        "id": kit.pk, "section": kit.section, "scope": kit.scope,
        "employee": kit.employee_id, "employee_name": kit.employee.full_name if kit.employee_id else "",
        "standards_text": kit.standards_text, "aux_text": kit.aux_text, "journal_aux": kit.journal_aux,
        "fif_numbers": list(kit.fif_numbers or []), "families": [f.pk for f in kit.families.all()],
        "family_names": [f.name for f in kit.families.all()], "note": kit.note, "sort": kit.sort,
        "is_active": kit.is_active, "updated_at": kit.updated_at,
    }


def aux_dict(item: AuxInstrument) -> dict:
    return {"id": item.pk, "name": item.name, "designation": item.designation, "note": item.note,
            "is_active": item.is_active, "sort": item.sort}


def search_kits(query: str = "", *, family=None, active_only: bool = True):
    qs = StandardKit.objects.select_related("employee").prefetch_related("families")
    if active_only:
        qs = qs.filter(is_active=True)
    if family:
        qs = qs.filter(families=family)
    for word in str(query or "").split():
        qs = qs.filter(Q(section__icontains=word) | Q(scope__icontains=word) | Q(standards_text__icontains=word)
                       | Q(aux_text__icontains=word))
    return qs.distinct()


def kits_for(si_type, template=None) -> list[StandardKit]:
    """Наборы, из которых поверитель выбирает свой: из шаблона поверки, иначе по семейству."""
    if template is not None and template.pk:
        own = list(template.kits.filter(is_active=True).select_related("employee"))
        if own:
            return own
    return list(search_kits(family=si_type.family_id).select_related("employee"))


def _set_fields(obj, data: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        if field in data:
            setattr(obj, field, _clean(data[field]))


def _apply_kit(op: str, data: dict, user) -> str:
    from apps.core.models import Employee

    if op == "add":
        kit = StandardKit()
        if not str(data.get("section") or "").strip():
            raise KitError("новая строка перечня: укажите section — раздел («Единый перечень для поверки манометров»)")
    else:
        kit = StandardKit.objects.filter(pk=data.get("id")).first()
        if kit is None:
            raise KitError(f"строки перечня с id {data.get('id')} нет")
    if op == "remove":
        kit.is_active = False
        kit.updated_by = user
        kit.save(update_fields=["is_active", "updated_by", "updated_at"])
        return f"выключена строка «{kit}»"
    _set_fields(kit, data, KIT_FIELDS)
    if "fif_numbers" in data:
        numbers = fif_numbers(data["fif_numbers"])
        raw = data["fif_numbers"]
        if not numbers and str(raw if not isinstance(raw, list) else "".join(map(str, raw))).strip():
            raise KitError(f"«{raw}» — не номер эталона в ФИФ ОЕИ (вида 68984.17.4Р.01152950)")
        kit.fif_numbers = numbers
    if "employee" in data:
        kit.employee = Employee.objects.filter(pk=data["employee"]).first() if data["employee"] else None
    if "is_active" in data:
        kit.is_active = bool(data["is_active"])
    elif op == "add":
        kit.is_active = True
    if "sort" in data:
        kit.sort = int(data["sort"] or 0)
    elif op == "add":
        kit.sort = (StandardKit.objects.order_by("-sort").values_list("sort", flat=True).first() or 0) + 1
    kit.updated_by = user if getattr(user, "pk", None) else None
    kit.save()
    if "families" in data:
        ids = [int(x) for x in (data["families"] or [])]
        families = list(MeasurementFamily.objects.filter(pk__in=ids))
        if len(families) != len(set(ids)):
            raise KitError("families — id семейств СИ из справочника")
        kit.families.set(families)
    elif op == "add":
        kit.families.set(guess_families(f"{kit.section} {kit.scope}"))
    return ("добавлена" if op == "add" else "изменена") + f" строка «{kit}»"


def _apply_aux(op: str, data: dict) -> str:
    if op == "add":
        item = AuxInstrument()
        if not str(data.get("name") or "").strip():
            raise KitError("новое вспомогательное СИ: укажите name")
    else:
        item = AuxInstrument.objects.filter(pk=data.get("id")).first()
        if item is None:
            raise KitError(f"вспомогательного СИ с id {data.get('id')} нет")
    if op == "remove":
        item.is_active = False
        item.save(update_fields=["is_active", "updated_at"])
        return f"выключено «{item}»"
    _set_fields(item, data, AUX_FIELDS)
    if item.designation and "^" not in item.designation:
        raise KitError(f"«{item.designation}»: обозначение для журнала — «номер в Госреестре^заводской^инвентарный»")
    item.is_active = bool(data.get("is_active", True)) if op == "add" or "is_active" in data else item.is_active
    item.save()
    return ("добавлено" if op == "add" else "изменено") + f" «{item}»"


@transaction.atomic
def apply_changes(changes: list, user=None) -> list[str]:
    """[{op: add|update|remove, target: kit|aux, id?, …поля}] → что сделано (всё или ничего)."""
    if not isinstance(changes, list) or not changes:
        raise KitError("changes — непустой список правок")
    done = []
    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            raise KitError(f"changes[{i}] — не объект")
        op, target = change.get("op"), change.get("target") or "kit"
        if op not in ("add", "update", "remove"):
            raise KitError(f"changes[{i}].op — add | update | remove")
        if target == "kit":
            done.append(_apply_kit(op, change, user))
        elif target == "aux":
            done.append(_apply_aux(op, change))
        else:
            raise KitError(f"changes[{i}].target — kit | aux")
    return done


def snapshot(kit: StandardKit) -> dict:
    """Что из набора попадает в поверку (протокол и журнал) — копия, а не ссылка."""
    return {
        "id": kit.pk, "section": kit.section, "scope": kit.scope, "standards_text": kit.standards_text,
        "aux_text": kit.aux_text, "journal_aux": kit.journal_aux, "fif_numbers": list(kit.fif_numbers or []),
    }
