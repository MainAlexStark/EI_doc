"""Каркас протокола аттестации — тоже часть плана.

Протоколы разных видов оборудования (и разных заказчиков) различаются не
только формулами, но и разделами, таблицами, формулировками. Поэтому вид
протокола задаётся в плане списком блоков (``layout``), который ассистент
снимает с примера. EI_doc только подставляет данные:

* обычные блоки ``apps.assistant.documents`` (heading, paragraph, fields,
  table, list, signatures, pagebreak, spacer) с подстановками ``{{…}}`` в тексте
  и ячейках;
* блоки-данные: commission, equipment_info, checked, conditions, documents,
  instruments, procedure, readings, results, conclusion, signatures (из комиссии);
* ``per_mode`` — повторить вложенные блоки для каждого режима.

У любого блока может быть ``"if": "путь"`` — блок выводится, только если
подстановка непустая.

Подстановки (``{{путь}}`` или ``{{путь|tolerance}}``):

    equipment.name / serial_number / manufacturer / year / owner / location / inventory_number
    number, protocol_date, performed_on, kind (периодической), kind_nom (периодическая)
    program, purpose, periodicity, quantity, Quantity (с заглавной), unit, verdict, conclusion
    conditions.<ключ>, texts.<ключ>
    mode.<режим>.label / setpoint / display / index / <параметр>
    result.<режим>.<характеристика>          — значение как в протоколе
    result.<режим>.<характеристика>.<точка>  — для характеристик «по точкам»
       |tolerance  |verdict  |formula  |label  — вместо значения
    mean.<режим>.<точка>, reading.<режим>.<точка>.<№ с 1>
Внутри per_mode вместо ключа режима — «_» (текущий режим).
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from apps.equipment import calc

BLANK = "________"
DOC_BLOCKS = ("heading", "paragraph", "fields", "table", "list", "signatures", "pagebreak", "spacer")
DATA_BLOCKS = (
    "commission", "equipment_info", "checked", "conditions", "documents", "instruments",
    "procedure", "readings", "results", "conclusion",
)
ALL_BLOCKS = (*DOC_BLOCKS, *DATA_BLOCKS, "per_mode")
RESULT_COLUMNS = {
    "label": "Характеристика", "formula": "Расчётная формула", "value": "Полученное значение",
    "tolerance": "Допускаемое значение", "verdict": "Соответствие",
}
ATTRS = ("value", "tolerance", "verdict", "formula", "label")
PLACEHOLDER = re.compile(r"\{\{\s*([^{}|]+?)\s*(?:\|\s*([A-Za-z_]+)\s*)?\}\}")
KIND = {
    "primary": ("первичной", "первичная"), "periodic": ("периодической", "периодическая"),
    "repeat": ("повторной", "повторная"),
}
COMMISSION_ROLES = {"chair": "Председатель комиссии", "member": "Член комиссии"}
EQUIPMENT_FIELDS = (
    ("name", "Наименование оборудования"), ("serial_number", "Заводской номер"),
    ("inventory_number", "Инвентарный номер"), ("manufacturer", "Изготовитель"), ("year", "Год выпуска"),
    ("owner", "Принадлежащее"), ("location", "Место установки"),
)
TEXT_KEYS = ("documentation", "inspection", "testing", "notes", "recommendations")
DEFAULT_TEXTS = {
    "documentation": "документация представлена в полном объеме на русском языке.",
    "inspection": "состояние удовлетворительное, комплектность и маркировка соответствуют требованиям "
                  "эксплуатационной документации, повреждений и дефектов покрытий нет.",
    "testing": "испытательное оборудование работоспособно и функционирует во всех режимах согласно "
               "эксплуатационной документации.",
}

DEFAULT_DOCUMENT = {
    "header": ["Общество с ограниченной ответственностью", "«ЕДИНИЦА ИЗМЕРЕНИЯ»"],
    "title": "Протокол {{kind}} аттестации испытательного оборудования",
    "subtitle": "№ {{number}} от {{protocol_date}}",
}
DEFAULT_CONCLUSION = {
    "ok": "На основании результатов {{kind}} аттестации установлено, что {{who}} соответствует требованиям "
          "эксплуатационной документации{{purpose_tail}}.",
    "fail": "На основании результатов {{kind}} аттестации установлено, что {{who}} не соответствует требованиям "
            "эксплуатационной документации: {{failed}}. К использованию не допускается до устранения "
            "несоответствий и повторной аттестации.",
}
DEFAULT_LAYOUT = [
    {"type": "heading", "text": "Состав комиссии"},
    {"type": "commission"},
    {"type": "heading", "text": "Основные сведения об испытательном оборудовании"},
    {"type": "equipment_info"},
    {"type": "heading", "text": "Проверяемые характеристики испытательного оборудования", "if": "checked"},
    {"type": "checked"},
    {"type": "heading", "text": "Условия проведения аттестации"},
    {"type": "conditions"},
    {"type": "heading", "text": "Документы, используемые для аттестации"},
    {"type": "documents"},
    {"type": "heading", "text": "Средства измерений, используемые при проведении аттестации"},
    {"type": "instruments"},
    {"type": "heading", "text": "Результаты аттестации"},
    {"type": "paragraph", "text": "1. Экспертиза эксплуатационной документации: {{texts.documentation}}"},
    {"type": "paragraph", "text": "2. Внешний осмотр: {{texts.inspection}}"},
    {"type": "paragraph", "text": "3. Опробование: {{texts.testing}}"},
    {"type": "procedure"},
    {"type": "paragraph", "text": "{{texts.notes}}", "if": "texts.notes"},
    {"type": "paragraph", "text": "4. Измерение и вычисление характеристик испытательного оборудования", "bold": True},
    {"type": "paragraph", "text": "Дата проведения измерений: {{performed_on}}."},
    {"type": "per_mode", "blocks": [
        {"type": "paragraph", "bold": True,
         "text": "4.{{mode._.index}} {{Quantity}}: заданное значение {{mode._.setpoint}} {{unit}}"},
        {"type": "paragraph", "text": "Показание индикатора ИО: {{mode._.display}} {{unit}}", "if": "mode._.display"},
        {"type": "readings"},
        {"type": "results"},
    ]},
    {"type": "heading", "text": "Заключение"},
    {"type": "conclusion"},
    {"type": "heading", "text": "Рекомендации комиссии"},
    {"type": "paragraph", "text": "Периодичность аттестации: {{periodicity}}.", "if": "periodicity"},
    {"type": "paragraph", "text": "{{texts.recommendations}}", "if": "texts.recommendations"},
    {"type": "signatures"},
]


def blocks_help() -> str:
    return (
        "layout — список блоков: " + ", ".join(ALL_BLOCKS) + ". Подстановки {{…}}: " +
        "equipment.*, number, protocol_date, performed_on, kind, kind_nom, program, purpose, periodicity, quantity, "
        "Quantity, unit, verdict, conclusion, conditions.<ключ>, texts.<ключ>, mode.<режим|_>.label|setpoint|display|"
        "index|<параметр>, result.<режим|_>.<характеристика>[.<точка>] с |tolerance|verdict|formula|label, "
        "mean.<режим|_>.<точка>, reading.<режим|_>.<точка>.<№>. readings{mode?, decimals?, mean?: bool}, "
        "results{mode?, columns?: [label, formula, value, tolerance, verdict]}, if: «путь» — показать блок, "
        "если значение непустое"
    )


# ---------------------------------------------------------------------------
# Проверка
# ---------------------------------------------------------------------------
def _known_path(path: str, attr: str | None, plan: dict, in_mode: bool, extra: set[str]) -> str | None:
    """None — путь допустим; иначе — что не так."""
    parts = path.split(".")
    modes = {m["key"] for m in plan["modes"]}
    if in_mode:
        modes = modes | {"_"}
    points = {p["key"] for p in plan["points"]}
    chars = {c["key"]: c for c in plan["characteristics"]}
    params = set(plan["modes"][0]["params"]) if plan["modes"] else set()
    head = parts[0]
    if attr and head != "result":
        return f"|{attr} бывает только у result.…"
    if attr and attr not in ATTRS:
        return f"|{attr}: можно {', '.join(ATTRS)}"
    simple = {
        "number", "protocol_date", "performed_on", "kind", "kind_nom", "program", "purpose", "periodicity",
        "quantity", "Quantity", "unit", "verdict", "conclusion", "checked", *extra,
    }
    if len(parts) == 1:
        return None if head in simple else f"неизвестная подстановка «{path}»"
    if head == "equipment":
        return None if len(parts) == 2 and parts[1] in dict(EQUIPMENT_FIELDS) else f"нет поля оборудования «{path}»"
    if head == "conditions":
        keys = {c["key"] for c in plan["conditions"]}
        return None if len(parts) == 2 and parts[1] in keys else f"нет условия «{path}» (есть: {', '.join(sorted(keys))})"
    if head == "texts":
        return None if len(parts) == 2 and parts[1] in TEXT_KEYS else f"нет текста «{path}» (есть: {', '.join(TEXT_KEYS)})"
    if head in ("mode", "result", "mean", "reading") and parts[1] not in modes:
        return f"«{path}»: нет режима «{parts[1]}»" + ("" if in_mode else " («_» — только внутри per_mode)")
    if head == "mode":
        ok = len(parts) == 3 and parts[2] in {"label", "setpoint", "display", "index", *params}
        return None if ok else f"«{path}»: mode.<режим>.label|setpoint|display|index|<параметр>"
    if head == "result":
        if len(parts) not in (3, 4) or parts[2] not in chars:
            return f"«{path}»: нет характеристики «{parts[2] if len(parts) > 2 else ''}»"
        per_point = chars[parts[2]]["per"] == "point"
        if per_point and len(parts) == 3 and attr in (None, "value", "verdict"):
            return f"«{path}»: характеристика «{parts[2]}» считается по точкам — укажите точку: …{parts[2]}.<точка>"
        if len(parts) == 4 and (not per_point or parts[3] not in points):
            return f"«{path}»: «{parts[3]}» — не точка этой характеристики"
        return None
    if head == "mean":
        return None if len(parts) == 3 and parts[2] in points else f"«{path}»: mean.<режим>.<точка>"
    if head == "reading":
        ok = len(parts) == 4 and parts[2] in points and parts[3].isdigit() and 1 <= int(parts[3]) <= plan["repeats"]
        return None if ok else f"«{path}»: reading.<режим>.<точка>.<№ от 1 до {plan['repeats']}>"
    return f"неизвестная подстановка «{path}»"


def _check_text(text: str, where: str, plan: dict, in_mode: bool, extra: set[str] = frozenset()) -> None:
    for match in PLACEHOLDER.finditer(text):
        problem = _known_path(match.group(1), match.group(2), plan, in_mode, set(extra))
        if problem:
            raise calc.PlanError(f"{where}: {problem}")
    if "{{" in PLACEHOLDER.sub("", text):
        raise calc.PlanError(f"{where}: незакрытая подстановка «{{{{…}}}}»")


def _clean_blocks(blocks, plan: dict, in_mode: bool, where: str) -> list[dict]:
    if not isinstance(blocks, list):
        raise calc.PlanError(f"{where}: список блоков")
    out = []
    modes = {m["key"] for m in plan["modes"]} | ({"_"} if in_mode else set())
    for i, raw in enumerate(blocks):
        here = f"{where}[{i}]"
        if not isinstance(raw, dict):
            raise calc.PlanError(f"{here}: не объект")
        kind = raw.get("type", "paragraph")
        if kind not in ALL_BLOCKS:
            raise calc.PlanError(f"{here}: неизвестный блок «{kind}». Есть: {', '.join(ALL_BLOCKS)}")
        block = dict(raw, type=kind)
        if "if" in block:
            _check_text("{{" + str(block["if"]) + "}}", f"{here}.if", plan, in_mode)
        if kind == "per_mode":
            if in_mode:
                raise calc.PlanError(f"{here}: per_mode внутри per_mode")
            block["blocks"] = _clean_blocks(raw.get("blocks") or [], plan, True, f"{here}.blocks")
        elif kind in ("readings", "results"):
            mode = raw.get("mode") or ("_" if in_mode else "")
            if mode and mode not in modes:
                raise calc.PlanError(f"{here}: нет режима «{mode}»")
            block["mode"] = mode
            if kind == "results":
                columns = raw.get("columns") or list(RESULT_COLUMNS)
                bad = [c for c in columns if c not in RESULT_COLUMNS]
                if bad:
                    raise calc.PlanError(f"{here}: колонки {', '.join(bad)} — можно {', '.join(RESULT_COLUMNS)}")
                block["columns"] = columns
                titles = raw.get("titles") or {}
                block["titles"] = {k: str(v)[:80] for k, v in titles.items() if k in RESULT_COLUMNS}
            else:
                block["mean"] = raw.get("mean", True) is not False
                decimals = raw.get("decimals", 3)
                block["decimals"] = decimals if isinstance(decimals, int) and 0 <= decimals <= 6 else 3
        else:
            for key in ("text", "label"):
                if key in block:
                    _check_text(str(block[key]), f"{here}.{key}", plan, in_mode)
            if kind == "table":
                for r, row in enumerate(block.get("rows") or []):
                    for c, cell in enumerate(row if isinstance(row, list) else [row]):
                        _check_text(str(cell), f"{here}.rows[{r}][{c}]", plan, in_mode)
            if kind == "list":
                for j, item in enumerate(block.get("items") or []):
                    _check_text(str(item), f"{here}.items[{j}]", plan, in_mode)
            if kind == "fields":
                for j, row in enumerate(block.get("rows") or []):
                    if isinstance(row, dict):
                        for key in ("label", "value", "note"):
                            _check_text(str(row.get(key, "")), f"{here}.rows[{j}].{key}", plan, in_mode)
        out.append(block)
    return out


def clean_document(raw: dict, plan: dict) -> dict:
    document = raw.get("document") or {}
    if not isinstance(document, dict):
        raise calc.PlanError("document — объект {header, title, subtitle}")
    header = document.get("header", DEFAULT_DOCUMENT["header"])
    header = [str(x)[:300] for x in (header if isinstance(header, list) else [header])]
    doc = {
        "header": header,
        "title": str(document.get("title") or DEFAULT_DOCUMENT["title"])[:300],
        "subtitle": str(document.get("subtitle") or DEFAULT_DOCUMENT["subtitle"])[:300],
    }
    for key in ("title", "subtitle"):
        _check_text(doc[key], f"document.{key}", plan, False)
    for i, line in enumerate(header):
        _check_text(line, f"document.header[{i}]", plan, False)

    conclusion = raw.get("conclusion") or {}
    if not isinstance(conclusion, dict):
        raise calc.PlanError("conclusion — объект {ok, fail}")
    concl = {k: str(conclusion.get(k) or DEFAULT_CONCLUSION[k])[:3000] for k in ("ok", "fail")}
    for key, text in concl.items():
        _check_text(text, f"conclusion.{key}", plan, False, {"who", "failed", "purpose_tail"})

    layout = raw.get("layout")
    cleaned = _clean_blocks(layout, plan, False, "layout") if layout else []
    if layout and not cleaned:
        raise calc.PlanError("layout пуст")
    return {"document": doc, "conclusion": concl, "layout": cleaned}


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------
def _date(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = dt.date.fromisoformat(value)
    return f"{value:%d.%m.%Y}"


class Context:
    """Всё, что нужно для подстановок одного протокола."""

    def __init__(self, *, plan, equipment, commission, instruments, texts, conditions, measurements,
                 results, kind, number, protocol_date, performed_on):
        self.plan, self.equipment, self.commission, self.instruments = plan, equipment, commission, instruments
        self.texts = {**DEFAULT_TEXTS, **(texts or {})}
        self.conditions, self.measurements, self.results = conditions or {}, measurements or {}, results
        self.kind, self.number, self.protocol_date, self.performed_on = kind, number, protocol_date, performed_on
        self.index = calc.results_index(results)
        self.modes = {m["key"]: (i + 1, m) for i, m in enumerate(plan["modes"])}
        self.current_mode: str | None = None

    # --- значения ---------------------------------------------------------
    def who(self) -> str:
        name = self.equipment.get("name", "")
        if len(name) > 1 and name[0].isupper() and name[1].islower():
            name = name[0].lower() + name[1:]  # «…установлено, что холодильник…», аббревиатуры не трогаем
        return f"{name}, заводской номер {self.equipment.get('serial_number', '')}"

    def failed(self) -> str:
        return "; ".join(
            f"{r['label']}{', точка ' + r['point_label'] if r['point'] else ''} ({m['label']}): {r['shown']} при "
            f"допуске {r['tolerance']}"
            for m in (self.results or {}).get("modes", []) for r in m["characteristics"] if r["passed"] is False
        )

    def conclusion(self) -> str:
        if self.results is None:
            return f"На основании результатов {KIND[self.kind][0]} аттестации установлено, что {self.who()} {BLANK}"
        template = self.plan["conclusion"]["ok" if self.results["suitable"] else "fail"]
        purpose = self.plan.get("purpose", "").rstrip(".")
        extra = {
            "who": self.who(), "failed": self.failed(),
            "purpose_tail": f" и может использоваться для {purpose}" if purpose else "",
        }
        return self.fill(template, extra)

    def value(self, path: str, attr: str | None = None, extra: dict | None = None) -> str:
        parts = path.split(".")
        if extra and path in extra:
            return extra[path]
        if len(parts) > 1 and parts[1] == "_" and self.current_mode:
            parts[1] = self.current_mode
        head = parts[0]
        p = self.plan
        if path == "conclusion":
            return self.conclusion()
        if len(parts) == 1:
            return {
                "number": f"{self.number}" if self.number else BLANK,
                "protocol_date": _date(self.protocol_date) or BLANK,
                "performed_on": _date(self.performed_on) or BLANK,
                "kind": KIND[self.kind][0], "kind_nom": KIND[self.kind][1],
                "program": p["program"], "purpose": p.get("purpose", ""), "periodicity": p.get("periodicity", ""),
                "quantity": p["quantity"], "Quantity": p["quantity"][:1].upper() + p["quantity"][1:],
                "unit": p["unit"], "checked": "да" if p.get("checked") else "",
                "verdict": "" if self.results is None else
                ("соответствует" if self.results["suitable"] else "не соответствует"),
            }.get(head, "")
        if head == "equipment":
            return self.equipment.get(parts[1], "")
        if head == "texts":
            return self.texts.get(parts[1], "")
        if head == "conditions":
            cond = next((c for c in p["conditions"] if c["key"] == parts[1]), None)
            raw = self.conditions.get(parts[1])
            return calc.fmt(Decimal(raw), cond["decimals"]) if raw not in (None, "") and cond else ""
        if head == "mode":
            index, mode = self.modes.get(parts[1], (0, None))
            if mode is None:
                return ""
            field = parts[2]
            if field == "index":
                return str(index)
            if field == "label":
                return mode["label"]
            if field == "setpoint":
                sp = Decimal(mode["setpoint"])
                return calc.fmt(sp, max(calc.places(sp), 1), sign=True)
            if field == "display":
                raw = (self.measurements.get(parts[1]) or {}).get("display")
                return calc.fmt(Decimal(raw), max(calc.places(Decimal(raw)), 1), sign=True) if raw else ""
            raw = mode["params"].get(field)
            return calc.fmt(Decimal(raw), calc.places(Decimal(raw))) if raw else ""
        if head == "result":
            row = self.index.get(parts[1], {}).get(parts[2])
            if isinstance(row, dict) and len(parts) == 4:
                row = row.get(parts[3])
            elif isinstance(row, dict) and "key" not in row:
                row = next(iter(row.values()), None) if attr in ("tolerance", "formula", "label") else None
            if row is None:
                char = next((c for c in p["characteristics"] if c["key"] == parts[2]), None)
                if char and attr == "formula":
                    return char["text"]
                if char and attr == "label":
                    return char["label"]
                return ""
            return {
                None: row["shown"], "value": row["shown"], "tolerance": row["tolerance"], "formula": row["formula"],
                "label": row["label"],
                "verdict": {True: "соответствует", False: "не соответствует", None: "—"}[row["passed"]],
            }[attr]
        if head == "mean":
            mode = next((m for m in (self.results or {}).get("modes", []) if m["key"] == parts[1]), None)
            pt = next((x for x in (mode or {}).get("points", []) if x["key"] == parts[2]), None)
            return calc.fmt(Decimal(pt["mean"]), 3) if pt else ""
        if head == "reading":
            values = ((self.measurements.get(parts[1]) or {}).get("readings") or {}).get(parts[2]) or []
            i = int(parts[3]) - 1
            return values[i].replace(".", ",") if i < len(values) and values[i] else ""
        return ""

    def fill(self, text: str, extra: dict | None = None) -> str:
        def sub(match):
            value = self.value(match.group(1), match.group(2), extra)
            return value if value != "" else BLANK

        return PLACEHOLDER.sub(sub, str(text))

    def present(self, path: str) -> bool:
        if path == "checked":
            return bool(self.plan.get("checked"))
        return self.value(path) not in ("", BLANK)


def _instrument_rows(ctx: Context) -> list[list[str]]:
    rows = []
    for r in ctx.instruments:
        until = f"действительно до {_date(r['valid_until'])}" if r.get("valid_until") else ""
        rows.append([r["name"], r["serial_number"], r["characteristics"],
                     " ".join(x for x in (r["verification"], until) if x)])
    return rows or [[BLANK, "", "", ""]]


def _readings_table(ctx: Context, mode_key: str, block: dict) -> dict:
    plan = ctx.plan
    n = plan["repeats"]
    data = ctx.measurements.get(mode_key) or {}
    result_mode = next((m for m in (ctx.results or {}).get("modes", []) if m["key"] == mode_key), None)
    rows = []
    for j, point in enumerate(plan["points"]):
        values = (data.get("readings") or {}).get(point["key"]) or [""] * n
        label = point["label"] + (" (к.т.)" if point["role"] == "control" and point["label"] == point["key"] else "")
        row = [label, *[v.replace(".", ",") if v else "" for v in values]]
        if block["mean"]:
            mean = result_mode["points"][j]["mean"] if result_mode else None
            row.append(calc.fmt(Decimal(mean), block["decimals"]) if mean else "")
        rows.append(row)
    columns = ["Точка", *[str(k + 1) for k in range(n)]] + (["Среднее"] if block["mean"] else [])
    widths = [10, *([6] * n)] + ([9] if block["mean"] else [])
    return {"type": "table", "size": 7.5 if n > 6 else 9, "columns": columns, "rows": rows, "widths": widths}


def _results_table(ctx: Context, mode_key: str, block: dict) -> dict:
    columns = block["columns"]
    titles = [block["titles"].get(c, RESULT_COLUMNS[c]) for c in columns]
    result_mode = next((m for m in (ctx.results or {}).get("modes", []) if m["key"] == mode_key), None)
    rows = []
    if result_mode:
        for r in result_mode["characteristics"]:
            label = r["label"] + (f", точка {r['point_label']}" if r["point"] else "")
            cells = {"label": label, "formula": r["formula"], "value": r["shown"], "tolerance": r["tolerance"],
                     "verdict": {True: "соответствует", False: "НЕ соответствует", None: "—"}[r["passed"]]}
            rows.append([cells[c] for c in columns])
    else:
        for c in ctx.plan["characteristics"]:
            cells = {"label": c["label"], "formula": c["text"], "value": "",
                     "tolerance": calc.plan_tolerance_text(c["tolerance"]), "verdict": ""}
            rows.append([cells[k] for k in columns])
    weights = {"label": 34, "formula": 22, "value": 14, "tolerance": 16, "verdict": 14}
    return {"type": "table", "columns": titles, "rows": rows, "widths": [weights[c] for c in columns]}


def _render(blocks: list[dict], ctx: Context, out: list[dict]) -> None:
    for block in blocks:
        if block.get("if") and not ctx.present(str(block["if"])):
            continue
        kind = block["type"]
        if kind == "per_mode":
            for mode in ctx.plan["modes"]:
                ctx.current_mode = mode["key"]
                _render(block["blocks"], ctx, out)
            ctx.current_mode = None
        elif kind == "commission":
            for r in ctx.commission:
                out.append(_p(f"{COMMISSION_ROLES[r['role']]}: " + " — ".join(x for x in (r["position"], r["name"]) if x)))
            if not ctx.commission:
                out.append(_p(BLANK))
        elif kind == "equipment_info":
            for key, label in EQUIPMENT_FIELDS:
                if ctx.equipment.get(key):
                    out.append(_p(f"{label}: {ctx.equipment[key]};"))
        elif kind == "checked":
            for x in ctx.plan.get("checked", []):
                out.append(_p(f"{x['label']}: {x['value']}"))
        elif kind == "conditions":
            for c in ctx.plan["conditions"]:
                value = ctx.value(f"conditions.{c['key']}") or "____"
                out.append(_p(f"{c['label']} – {value} {c['unit']}".rstrip()))
        elif kind == "documents":
            docs = list(ctx.plan.get("documents", []))
            program = ctx.plan["program"]
            if not any(program in d or d in program for d in docs):
                docs.insert(min(1, len(docs)), program)
            out.append({"type": "list", "items": docs})
        elif kind == "instruments":
            out.append({
                "type": "table",
                "columns": ["Наименование СИ, тип СИ", "Заводской номер", "Диапазон измерений, погрешность",
                            "Сведения о поверке"],
                "rows": _instrument_rows(ctx), "widths": [30, 14, 28, 28],
            })
        elif kind == "procedure":
            out.extend(_p(t) for t in ctx.plan.get("procedure", []))
        elif kind in ("readings", "results"):
            mode = block["mode"]
            keys = [ctx.current_mode] if mode == "_" else [mode] if mode else [m["key"] for m in ctx.plan["modes"]]
            for key in keys:
                out.append((_readings_table if kind == "readings" else _results_table)(ctx, key, block))
        elif kind == "conclusion":
            out.append(_p(ctx.conclusion()))
        elif kind == "signatures":
            rows = block.get("rows") or [
                {"role": COMMISSION_ROLES[r["role"]], "name": r["name"]} for r in ctx.commission
            ]
            out.append({"type": "signatures", "rows": [
                {"role": ctx.fill(r.get("role", "")), "name": ctx.fill(r.get("name", ""))} for r in rows
            ]})
        else:
            filled = dict(block)
            filled.pop("if", None)
            for key in ("text", "label"):
                if key in filled:
                    filled[key] = ctx.fill(filled[key])
            if kind == "paragraph":
                filled.setdefault("align", "justify")
            if kind == "table":
                filled["rows"] = [[ctx.fill(c) for c in (row if isinstance(row, list) else [row])]
                                  for row in filled.get("rows") or []]
                filled["columns"] = [ctx.fill(c) for c in filled.get("columns") or []]
            if kind == "list":
                filled["items"] = [ctx.fill(x) for x in filled.get("items") or []]
            if kind == "fields":
                filled["rows"] = [{k: ctx.fill(v) for k, v in r.items()} for r in filled.get("rows") or []
                                  if isinstance(r, dict)]
            out.append(filled)


def _p(text: str) -> dict:
    return {"type": "paragraph", "text": text, "align": "justify"}


def build(ctx: Context, *, draft: bool) -> dict:
    """Протокол блоками apps.assistant.documents — один источник для Word и PDF."""
    blocks: list[dict] = []
    _render(ctx.plan.get("layout") or clean_default_layout(), ctx, blocks)
    doc = ctx.plan.get("document") or DEFAULT_DOCUMENT
    number_blank = ctx.number is None
    subtitle = doc["subtitle"]
    if number_blank and subtitle == DEFAULT_DOCUMENT["subtitle"]:
        subtitle = f"№ {BLANK} от {BLANK}"
    return {
        "header": [ctx.fill(x) for x in doc["header"]],
        "title": ctx.fill(doc["title"]),
        "subtitle": ctx.fill(subtitle),
        "draft": draft,
        "footer": "Черновик — расчёт по текущим данным, не для выдачи" if draft else "",
        "blocks": blocks,
    }


def clean_default_layout() -> list[dict]:
    # Блоки по умолчанию уже в каноническом виде, кроме полей, которые добавляет проверка.
    out = []
    for block in DEFAULT_LAYOUT:
        if block["type"] == "per_mode":
            inner = []
            for b in block["blocks"]:
                b = dict(b)
                if b["type"] == "readings":
                    b.update(mode="_", mean=True, decimals=3)
                if b["type"] == "results":
                    b.update(mode="_", columns=list(RESULT_COLUMNS), titles={})
                inner.append(b)
            out.append({**block, "blocks": inner})
        else:
            out.append(dict(block))
    return out
