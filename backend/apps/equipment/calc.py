"""План аттестации, расчёт и сверка с примером — чистые функции без БД.

Как делятся обязанности (см. docs/attestation.md):

* **План** — что измерять и как считать: точки и их группы, режимы,
  характеристики с формулами-выражениями (``apps.equipment.expr``), допуски с
  источниками, каркас протокола. Собирает ассистент из примера протокола,
  прошлой аттестации и программы/методики; проверяет метролог. Формулы не
  зашиты в код: у каждой методики свои.
* **Сверка с примером** — если в примере есть числа, план прогоняется на них
  и результаты сравниваются с напечатанными в примере (``check_example``).
  Так видно, правильно ли ассистент понял методику.
* **Измерения** — только человек с экрана; расчёт не начинается, пока сетка
  не заполнена целиком.
* **Расчёт и вывод** — здесь: Decimal, годность — сравнение с допусками.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from apps.equipment import expr
from apps.equipment.expr import ExprError, Group, Missing, Vec

ROLES = ("volume", "control")
TOLERANCE_KINDS = ("range", "abs", "none")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
RESERVED = {"setpoint", "display", "n", "repeats", "x", "point", "all", *expr.FUNCTIONS}

DEFAULT_CONDITIONS = [
    {"key": "temperature", "label": "Температура окружающего воздуха", "unit": "°С", "decimals": 1},
    {"key": "humidity", "label": "Относительная влажность воздуха", "unit": "%", "decimals": 1},
    {"key": "pressure", "label": "Атмосферное давление", "unit": "кПа", "decimals": 1},
]

# Готовые формулы — подсказка, а не ограничение: можно сослаться ``preset`` или
# написать своё выражение ``expr``.
PRESETS: dict[str, dict] = {
    "mean_all": {"label": "Достигнутое значение температуры, °С", "expr": "mean(all)", "text": "Тдост = Σ tᵢ / n"},
    "deviation_from_setpoint": {
        "label": "Отклонение достигнутого значения температуры от заданного, °С",
        "expr": "mean(all) - setpoint", "text": "ΔT = Тдост − Тзад",
    },
    "max_abs_deviation": {
        "label": "Наибольшее отклонение от заданного значения, °С",
        "expr": "max(abs(all - setpoint))", "text": "ΔTmax = max |tᵢ − Тзад|",
    },
    "display_deviation": {
        "label": "Отклонение показаний индикатора ИО от достигнутого значения, °С",
        "expr": "display - mean(all)", "text": "Δинд = Тинд − Тдост",
    },
    "amplitude_control": {
        "label": "Амплитуда колебания температуры в контрольной точке, °С",
        "expr": "(max(control) - min(control)) / 2", "text": "А = (tmax к.т. − tmin к.т.) / 2",
    },
    "variation_max": {
        "label": "Вариация температуры в полезном объёме камеры (к наибольшему), °С",
        "expr": "mean(control) - max(means(volume))", "text": "Δt₁ = tср.к.т. − tср.max",
    },
    "variation_min": {
        "label": "Вариация температуры в полезном объёме камеры (к наименьшему), °С",
        "expr": "mean(control) - min(means(volume))", "text": "Δt₂ = tср.к.т. − tср.min",
    },
    "gradient": {
        "label": "Градиент температуры, °С", "expr": "max(means(volume)) - min(means(volume))",
        "text": "tград = tср.max − tср.min",
    },
}
FORMULAS = PRESETS  # старое имя, ассистент и экран показывают его как подсказку


class PlanError(ValueError):
    """План не прошёл проверку — сообщение для человека."""


class Incomplete(ValueError):
    """Не хватает измерений — расчёт невозможен."""


# ---------------------------------------------------------------------------
# Числа
# ---------------------------------------------------------------------------
def number(value, *, what: str = "значение") -> Decimal:
    if isinstance(value, bool) or value is None:
        raise PlanError(f"{what}: нужно число")
    text = str(value).strip().replace(" ", "").replace(",", ".").replace("−", "-").replace("–", "-")
    text = text.removeprefix("+")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise PlanError(f"{what}: «{value}» — не число") from exc
    if not result.is_finite():
        raise PlanError(f"{what}: «{value}» — не число")
    return result


def is_number(value) -> bool:
    try:
        number(value)
        return True
    except PlanError:
        return False


def fmt(value: Decimal | None, decimals: int = 2, *, sign: bool = False) -> str:
    """Число для протокола: десятичная запятая, фиксированное число знаков."""
    if value is None:
        return "—"
    q = Decimal(1).scaleb(-decimals) if decimals > 0 else Decimal(1)
    rounded = value.quantize(q, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)  # без «-0,00»
    text = f"{rounded:f}".replace(".", ",")
    if sign and rounded > 0:
        text = "+" + text
    return text


def places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return max(0, -int(exponent)) if isinstance(exponent, int) else 0


def _mean(values) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


# ---------------------------------------------------------------------------
# Проверка плана
# ---------------------------------------------------------------------------
def _text(value, what: str, *, required: bool = True, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PlanError(f"не заполнено: {what}")
    return text[:limit]


def _key(value, what: str) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 20:
        raise PlanError(f"{what}: нужен короткий ключ (1–20 символов)")
    return key


def _ident(value, what: str) -> str:
    key = str(value or "").strip()
    if not IDENT.match(key):
        raise PlanError(f"{what} «{key}»: латиница, цифры и _, начинается с буквы — на него ссылаются формулы")
    if key in RESERVED:
        raise PlanError(f"{what} «{key}»: имя занято ({', '.join(sorted(RESERVED))})")
    return key


def _bound(raw, what: str, allowed: set[str]) -> str:
    """Граница допуска: число или выражение от setpoint и параметров режима."""
    if raw is None or str(raw).strip() == "":
        raise PlanError(f"{what}: не указано")
    if is_number(raw):
        return str(number(raw))
    try:
        unknown = expr.names(str(raw)) - allowed
        if expr.point_refs(str(raw)):
            raise ExprError("в допуске не может быть point(…)")
    except ExprError as exc:
        raise PlanError(f"{what}: {exc}") from exc
    if unknown:
        raise PlanError(f"{what}: в допуске можно использовать только setpoint и параметры режима, а не {', '.join(sorted(unknown))}")
    return expr.normalize(str(raw))


def clean_tolerance(raw, what: str, allowed: set[str]) -> dict:
    if raw in (None, "", {}):
        return {"kind": "none"}
    if not isinstance(raw, dict):
        raise PlanError(f"{what}: допуск — объект {{kind, …}}")
    kind = raw.get("kind", "none")
    if kind not in TOLERANCE_KINDS:
        raise PlanError(f"{what}: kind допуска — {' | '.join(TOLERANCE_KINDS)}")
    if kind == "range":
        out = {"kind": "range", "min": _bound(raw.get("min"), f"{what}: min", allowed),
               "max": _bound(raw.get("max"), f"{what}: max", allowed)}
        if is_number(out["min"]) and is_number(out["max"]) and number(out["min"]) > number(out["max"]):
            raise PlanError(f"{what}: min больше max")
        return out
    if kind == "abs":
        out = {"kind": "abs", "limit": _bound(raw.get("limit"), f"{what}: limit", allowed)}
        if is_number(out["limit"]) and number(out["limit"]) < 0:
            raise PlanError(f"{what}: limit не может быть отрицательным")
        return out
    return {"kind": "none"}


def clean_plan(raw) -> dict:
    """Проверить план и привести к каноническому виду. Ошибки — для человека."""
    if not isinstance(raw, dict):
        raise PlanError("план — объект")
    forbidden = {"measurements", "readings", "results", "values"} & set(raw)
    if forbidden:
        raise PlanError(
            "в плане не может быть результатов измерений (" + ", ".join(sorted(forbidden)) + ") — "
            "их вносит человек на экране аттестации; числа примера передаются только на сверку (example)"
        )

    repeats = raw.get("repeats")
    if not isinstance(repeats, int) or isinstance(repeats, bool) or not 1 <= repeats <= 100:
        raise PlanError("repeats — число измерений в каждой точке, целое от 1 до 100")

    # Точки и группы
    points, seen, groups = [], set(), {"all"}
    for i, p in enumerate(raw.get("points") or []):
        if not isinstance(p, dict):
            raise PlanError(f"points[{i}] — не объект")
        key = _key(p.get("key"), f"points[{i}].key")
        if key in seen:
            raise PlanError(f"точка «{key}» указана дважды")
        seen.add(key)
        role = p.get("role", "volume")
        if role not in ROLES:
            raise PlanError(f"points[{i}].role — volume (рабочий объём) | control (контрольная точка)")
        extra = [_ident(g, f"points[{i}]: группа") for g in (p.get("groups") or [])]
        point_groups = list(dict.fromkeys([role, *extra]))
        groups.update(point_groups)
        points.append({
            "key": key, "label": _text(p.get("label") or key, "подпись точки", limit=80),
            "role": role, "groups": point_groups,
        })
    if not points:
        raise PlanError("нет точек измерений (points)")
    if len(points) > 60:
        raise PlanError("больше 60 точек — проверьте план")
    groups |= {"volume", "control"}  # всегда есть, пусть и пустые

    # Режимы
    modes, seen, params = [], set(), set()
    for i, m in enumerate(raw.get("modes") or []):
        if not isinstance(m, dict):
            raise PlanError(f"modes[{i}] — не объект")
        key = _key(m.get("key") or f"m{i + 1}", f"modes[{i}].key")
        if key in seen:
            raise PlanError(f"режим «{key}» указан дважды")
        seen.add(key)
        setpoint = number(m.get("setpoint"), what=f"modes[{i}].setpoint (заданное значение)")
        mode_params = {}
        for name, value in (m.get("params") or {}).items():
            mode_params[_ident(name, f"modes[{i}]: параметр")] = str(number(value, what=f"modes[{i}].params.{name}"))
        params |= set(mode_params)
        modes.append({
            "key": key, "setpoint": str(setpoint), "params": mode_params,
            "label": _text(m.get("label") or f"{fmt(setpoint, places(setpoint), sign=True)}", "подпись режима", limit=80),
        })
    if not modes:
        raise PlanError("нет режимов (modes) — хотя бы одно заданное значение")
    missing_params = [m["label"] for m in modes if set(m["params"]) != params]
    if missing_params:
        raise PlanError(f"у режимов {', '.join(missing_params)} не хватает параметров: в каждом режиме нужны {', '.join(sorted(params))}")
    if groups & params:
        raise PlanError(f"имена {', '.join(sorted(groups & params))} — и группа точек, и параметр режима")

    # Характеристики
    base = {"setpoint", "display", "n", "repeats", *groups, *params}
    characteristics, seen = [], set()
    for i, c in enumerate(raw.get("characteristics") or []):
        if not isinstance(c, dict):
            raise PlanError(f"characteristics[{i}] — не объект")
        preset = PRESETS.get(c.get("preset") or c.get("formula") or "")
        if (c.get("preset") or c.get("formula")) and not preset:
            raise PlanError(
                f"characteristics[{i}]: нет готовой формулы «{c.get('preset') or c.get('formula')}» — "
                f"напишите выражение в expr (готовые: {', '.join(PRESETS)})"
            )
        source_expr = c.get("expr") or (preset or {}).get("expr")
        key = _ident(c.get("key") or c.get("preset") or c.get("formula"), f"characteristics[{i}].key")
        if key in seen or key in base:
            raise PlanError(f"характеристика «{key}»: имя уже занято")
        label = _text(c.get("label") or (preset or {}).get("label"), f"название характеристики «{key}»", limit=200)
        per = c.get("per") or "mode"
        if per not in ("mode", "point"):
            raise PlanError(f"«{label}»: per — mode (одно значение на режим) | point (по каждой точке)")
        group = c.get("group") or "all"
        if per == "point" and group not in groups:
            raise PlanError(f"«{label}»: нет группы точек «{group}»")
        allowed = base | seen | ({"x"} if per == "point" else set())
        try:
            unknown = expr.names(source_expr) - allowed
            bad_points = expr.point_refs(source_expr) - {p["key"] for p in points}
        except ExprError as exc:
            raise PlanError(f"«{label}»: {exc}") from exc
        if unknown:
            raise PlanError(
                f"«{label}»: неизвестные имена {', '.join(sorted(unknown))}. Доступны: группы "
                f"{', '.join(sorted(groups))}; setpoint, display, n, repeats"
                + (", x (отсчёты точки)" if per == "point" else "")
                + (f"; параметры режима {', '.join(sorted(params))}" if params else "")
                + (f"; характеристики выше: {', '.join(seen)}" if seen else "")
            )
        if bad_points:
            raise PlanError(f"«{label}»: нет точек {', '.join(sorted(bad_points))}")
        decimals = c.get("decimals", 2)
        if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 6:
            raise PlanError(f"«{label}»: decimals — от 0 до 6")
        tolerance = clean_tolerance(c.get("tolerance"), f"«{label}»", {"setpoint", *params})
        source = _text(c.get("source"), f"источник «{key}»", required=False, limit=300)
        if tolerance["kind"] != "none" and not source:
            raise PlanError(f"у допуска «{label}» не указан источник (пункт программы/методики, РЭ, пример)")
        seen.add(key)
        characteristics.append({
            "key": key, "label": label, "expr": expr.normalize(source_expr),
            "text": _text(c.get("text") or (preset or {}).get("text") or source_expr, "формула для протокола", limit=200),
            "per": per, "group": group if per == "point" else "", "decimals": decimals,
            "tolerance": tolerance, "source": source,
        })
    if not characteristics:
        raise PlanError("нет характеристик (characteristics)")
    if not any(c["tolerance"]["kind"] != "none" for c in characteristics):
        raise PlanError("ни у одной характеристики нет допуска — годность не из чего выводить")

    # Условия проведения
    conditions, seen_c = [], set()
    for i, cond in enumerate(raw.get("conditions") or DEFAULT_CONDITIONS):
        if not isinstance(cond, dict):
            raise PlanError(f"conditions[{i}] — не объект")
        key = _ident(cond.get("key"), f"conditions[{i}].key")
        if key in seen_c:
            raise PlanError(f"условие «{key}» указано дважды")
        seen_c.add(key)
        decimals = cond.get("decimals", 1)
        conditions.append({
            "key": key, "label": _text(cond.get("label"), f"conditions[{i}].label", limit=120),
            "unit": _text(cond.get("unit"), "единица", required=False, limit=20),
            "decimals": decimals if isinstance(decimals, int) and 0 <= decimals <= 4 else 1,
        })

    plan = {
        "equipment_kind": _text(raw.get("equipment_kind"), "вид оборудования", limit=200),
        "program": _text(raw.get("program"), "программа и методика аттестации", limit=400),
        "documents": [_text(d, "документ", limit=400) for d in (raw.get("documents") or []) if str(d).strip()],
        "quantity": _text(raw.get("quantity") or "температура", "величина", limit=80),
        "unit": _text(raw.get("unit") or "°С", "единица", limit=20),
        "checked": [
            {"label": _text(x.get("label"), "проверяемая характеристика", limit=200),
             "value": _text(x.get("value"), "значение проверяемой характеристики", limit=200)}
            for x in (raw.get("checked") or []) if isinstance(x, dict)
        ],
        "repeats": repeats,
        "points": points,
        "modes": modes,
        "characteristics": characteristics,
        "conditions": conditions,
        "procedure": [_text(t, "абзац методики", limit=3000) for t in (raw.get("procedure") or []) if str(t).strip()],
        "purpose": _text(raw.get("purpose"), "назначение ИО (для заключения)", required=False, limit=600),
        "periodicity": _text(raw.get("periodicity"), "периодичность", required=False, limit=120),
    }

    from apps.equipment import (
        layout,  # каркас протокола — отдельный модуль, ссылается на план
    )

    plan.update(layout.clean_document(raw, plan))

    _dry_run(plan)
    return plan


def _dry_run(plan: dict) -> None:
    """Прогнать формулы на условных данных: ловит «ряд вместо числа», деление на ноль в структуре и т. п."""
    fake = {}
    for mode in plan["modes"]:
        sp = Decimal(mode["setpoint"])
        readings = {
            p["key"]: [str(sp + Decimal((i * 7 + j * 3) % 11 - 5) / 100) for j in range(plan["repeats"])]
            for i, p in enumerate(plan["points"])
        }
        fake[mode["key"]] = {"display": str(sp), "readings": readings}
    try:
        compute(plan, fake)
    except Missing:
        pass
    except (ExprError, PlanError) as exc:
        raise PlanError(f"формулы плана не считаются даже на пробных данных: {exc}") from exc


# ---------------------------------------------------------------------------
# Измерения
# ---------------------------------------------------------------------------
def clean_measurements(plan: dict, raw) -> tuple[dict, list[str]]:
    """Привести внесённые человеком значения к строкам-числам.

    Пустые клетки допустимы (черновик) — возвращается список того, чего не хватает.
    Лишние ключи (точки/режимы не из плана) — ошибка: значит, план поменялся.
    """
    raw = raw if isinstance(raw, dict) else {}
    mode_keys = {m["key"] for m in plan["modes"]}
    point_keys = {p["key"] for p in plan["points"]}
    unknown = set(raw) - mode_keys
    if unknown:
        raise PlanError(f"режимов {', '.join(sorted(unknown))} нет в плане")
    uses_display = any("display" in expr.names(c["expr"]) for c in plan["characteristics"])
    clean, missing = {}, []
    for mode in plan["modes"]:
        data = raw.get(mode["key"]) or {}
        readings = data.get("readings") or {}
        extra = set(readings) - point_keys
        if extra:
            raise PlanError(f"точек {', '.join(sorted(extra))} нет в плане")
        out = {"display": "", "readings": {}}
        if str(data.get("display", "")).strip():
            out["display"] = str(number(data["display"], what=f"{mode['label']}: показание индикатора"))
        for point in plan["points"]:
            values = list(readings.get(point["key"]) or [])
            if len(values) > plan["repeats"]:
                raise PlanError(f"{mode['label']}, точка {point['label']}: больше {plan['repeats']} значений")
            row = []
            for i in range(plan["repeats"]):
                cell = values[i] if i < len(values) else ""
                if cell is None or str(cell).strip() == "":
                    row.append("")
                else:
                    row.append(str(number(cell, what=f"{mode['label']}, точка {point['label']}, №{i + 1}")))
            empty = row.count("")
            if empty:
                missing.append(f"{mode['label']}, точка {point['label']}: не внесено {empty} из {plan['repeats']}")
            out["readings"][point["key"]] = row
        if not out["display"] and uses_display:
            missing.append(f"{mode['label']}: показание индикатора ИО")
        clean[mode["key"]] = out
    return clean, missing


# ---------------------------------------------------------------------------
# Расчёт
# ---------------------------------------------------------------------------
def _mode_env(plan: dict, mode: dict, data: dict) -> tuple[dict, dict[str, Vec]]:
    vectors = {p["key"]: Vec(tuple(Decimal(v) for v in data["readings"][p["key"]])) for p in plan["points"]}
    group_names = {"all", "volume", "control"} | {g for p in plan["points"] for g in p["groups"]}
    env: dict = {
        name: Group(name, tuple(
            (p["key"], vectors[p["key"]]) for p in plan["points"] if name == "all" or name in p["groups"]
        ))
        for name in group_names
    }
    all_count = sum(len(v.values) for v in vectors.values())
    env.update({
        "setpoint": Decimal(mode["setpoint"]),
        "display": Decimal(data["display"]) if data.get("display") else None,
        "n": Decimal(all_count), "repeats": Decimal(plan["repeats"]),
        **{k: Decimal(v) for k, v in mode["params"].items()},
    })
    return env, vectors


def tolerance_values(tol: dict, env: dict) -> dict:
    """Границы допуска числами для режима (выражения от setpoint считаются здесь)."""
    def value(raw):
        return number(raw) if is_number(raw) else expr.evaluate_number(raw, env)

    if tol["kind"] == "range":
        return {"kind": "range", "min": value(tol["min"]), "max": value(tol["max"])}
    if tol["kind"] == "abs":
        return {"kind": "abs", "limit": value(tol["limit"])}
    return {"kind": "none"}


def tolerance_text(tol: dict) -> str:
    """tol — со значениями-числами (после tolerance_values)."""
    if tol["kind"] == "range":
        lo, hi = Decimal(tol["min"]), Decimal(tol["max"])
        return f"от {fmt(lo, places(lo), sign=True)} до {fmt(hi, places(hi), sign=True)}"
    if tol["kind"] == "abs":
        limit = Decimal(tol["limit"])
        return "±" + fmt(limit, places(limit))
    return "—"


def plan_tolerance_text(tol: dict) -> str:
    """Допуск как записан в плане — для экрана плана (там ещё нет режима)."""
    def show(raw):
        return fmt(number(raw), places(number(raw)), sign=True) if is_number(raw) else raw

    if tol["kind"] == "range":
        return f"от {show(tol['min'])} до {show(tol['max'])}"
    if tol["kind"] == "abs":
        return "±" + (fmt(number(tol["limit"]), places(number(tol["limit"]))) if is_number(tol["limit"]) else tol["limit"])
    return "—"


def check(value: Decimal, tol: dict, decimals: int) -> bool | None:
    """Годность значения по допуску. Сравнивается округлённое — как в протоколе."""
    shown = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if tol["kind"] == "range":
        return tol["min"] <= shown <= tol["max"]
    if tol["kind"] == "abs":
        return abs(shown) <= tol["limit"]
    return None


def compute(plan: dict, measurements: dict) -> dict:
    """Рассчитать характеристики и годность. Бросает Incomplete, если сетка не полная."""
    clean, missing = clean_measurements(plan, measurements)
    if missing:
        raise Incomplete("; ".join(missing))
    modes_out, passed_all = [], True
    for mode in plan["modes"]:
        data = clean[mode["key"]]
        env, vectors = _mode_env(plan, mode, data)
        points = [
            {"key": p["key"], "label": p["label"], "role": p["role"], "readings": data["readings"][p["key"]],
             "mean": str(_mean(vectors[p["key"]].values))}
            for p in plan["points"]
        ]
        rows = []
        for c in plan["characteristics"]:
            try:
                tol = tolerance_values(c["tolerance"], env)
            except ExprError as exc:
                raise PlanError(f"«{c['label']}», допуск: {exc}") from exc
            tol_text = tolerance_text(tol)
            try:
                if c["per"] == "point":
                    group = env[c["group"]]
                    values = []
                    for point_key, vec in group.points:
                        value = expr.evaluate_number(c["expr"], {**env, "x": vec}, vectors)
                        values.append(value)
                        ok = check(value, tol, c["decimals"])
                        passed_all &= ok is not False
                        rows.append(_row(c, value, tol_text, ok, point_key, plan))
                    env[c["key"]] = Vec(tuple(values))
                else:
                    value = expr.evaluate_number(c["expr"], env, vectors)
                    ok = check(value, tol, c["decimals"])
                    passed_all &= ok is not False
                    rows.append(_row(c, value, tol_text, ok, None, plan))
                    env[c["key"]] = value
            except Missing:
                raise
            except ExprError as exc:
                raise PlanError(f"«{c['label']}» ({mode['label']}): {exc}") from exc
        modes_out.append({
            "key": mode["key"], "label": mode["label"], "setpoint": mode["setpoint"],
            "display": data["display"], "points": points, "characteristics": rows,
            "passed": all(r["passed"] is not False for r in rows),
        })
    return {"modes": modes_out, "suitable": passed_all}


def _row(c: dict, value: Decimal, tol_text: str, ok, point_key, plan) -> dict:
    point_label = next((p["label"] for p in plan["points"] if p["key"] == point_key), "") if point_key else ""
    return {
        "key": c["key"], "label": c["label"], "formula": c["text"], "point": point_key or "",
        "point_label": point_label, "value": str(value), "shown": fmt(value, c["decimals"]),
        "tolerance": tol_text, "passed": ok,
    }


def results_index(results: dict | None) -> dict:
    """{режим: {характеристика: строка | {точка: строка}}} — для подстановок в протокол."""
    index: dict = {}
    for m in (results or {}).get("modes", []):
        bucket = index.setdefault(m["key"], {})
        for r in m["characteristics"]:
            if r["point"]:
                bucket.setdefault(r["key"], {})[r["point"]] = r
            else:
                bucket[r["key"]] = r
    return index


# ---------------------------------------------------------------------------
# Сверка с примером
# ---------------------------------------------------------------------------
def _expected_tolerance(raw) -> Decimal:
    """Расхождение в пределах единицы последнего напечатанного знака (не точнее 1e-6)."""
    text = str(raw).strip().replace(",", ".")
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    return Decimal(1).scaleb(-min(decimals, 6))


def check_example(plan: dict, example) -> dict:
    """Прогнать план на числах примера и сравнить с результатами, напечатанными в примере.

    example: {"source": "…", "modes": {режим: {"display"?, "readings": {точка: [..]},
              "expected": {характеристика: "5,69" | {точка: "…"}}}}}
    Числа примера никуда не сохраняются как измерения — только отчёт.
    """
    if not isinstance(example, dict) or not isinstance(example.get("modes"), dict):
        raise PlanError("example: {source, modes: {режим: {display?, readings, expected}}}")
    measurements = {k: {"display": v.get("display", ""), "readings": v.get("readings") or {}}
                    for k, v in example["modes"].items() if isinstance(v, dict)}
    unknown_modes = set(measurements) - {m["key"] for m in plan["modes"]}
    if unknown_modes:
        raise PlanError(f"example: режимов {', '.join(sorted(unknown_modes))} нет в плане")
    modes = [m for m in plan["modes"] if m["key"] in measurements]
    if not modes:
        raise PlanError("example: нет ни одного режима из плана")
    sub_plan = {**plan, "modes": modes}
    try:
        results = compute(sub_plan, measurements)
    except Incomplete as exc:
        raise PlanError(f"example: в примере не полная сетка под план — {exc}. Проверьте repeats и точки") from exc
    index = results_index(results)
    rows, errors = [], []
    checked_keys = set()
    for mode_key, data in example["modes"].items():
        for char_key, expected in (data.get("expected") or {}).items():
            got = index.get(mode_key, {}).get(char_key)
            if got is None:
                errors.append(f"{mode_key}: в плане нет характеристики «{char_key}»")
                continue
            checked_keys.add(char_key)
            pairs = (
                [(pk, expected[pk], got.get(pk)) for pk in expected] if isinstance(expected, dict)
                else [("", expected, got)]
            )
            for point_key, exp_raw, row in pairs:
                if row is None or (isinstance(row, dict) and "value" not in row):
                    errors.append(f"{mode_key}/{char_key}: нет значения для точки «{point_key}»")
                    continue
                try:
                    exp = number(exp_raw, what=f"ожидаемое {char_key}")
                except PlanError as exc:
                    errors.append(str(exc))
                    continue
                value = Decimal(row["value"])
                diff = value - exp
                ok = abs(diff) <= _expected_tolerance(exp_raw)
                rows.append({
                    "mode": mode_key, "key": char_key, "label": row["label"], "point": point_key,
                    "expected": str(exp_raw), "computed": fmt(value, max(places(exp) + 1, 3)),
                    "diff": str(diff), "ok": ok,
                })
    unchecked = [c["key"] for c in plan["characteristics"] if c["key"] not in checked_keys]
    return {
        "source": str(example.get("source") or "")[:300],
        "ok": bool(rows) and all(r["ok"] for r in rows) and not errors,
        "rows": rows, "errors": errors, "unchecked": unchecked,
    }


def plan_help() -> str:
    """Описание формата плана для ассистента (в описание инструментов)."""
    from apps.equipment import layout

    presets = "; ".join(f"{k}: {v['expr']}" for k, v in PRESETS.items())
    return (
        "plan{equipment_kind, program, documents[], quantity, unit, checked[{label,value}], repeats, "
        "points[{key, label, role: volume|control, groups?: [имя_группы]}], "
        "modes[{key, label, setpoint, params?: {имя: число}}], "
        "characteristics[{key (латиница — на него ссылаются формулы ниже), label, expr, text (как формула "
        "напечатана в протоколе), per?: mode|point (point — значение по каждой точке группы group, отсчёты точки — x), "
        "decimals, tolerance{kind: range{min,max} | abs{limit} | none} — границы числом или выражением от "
        "setpoint и params, source}], conditions?[{key, label, unit, decimals}] (по умолчанию температура, "
        "влажность, давление), procedure[абзацы], purpose, periodicity, document?{header[], title, subtitle}, "
        "conclusion?{ok, fail} (подстановки {{who}}, {{failed}}, {{purpose_tail}}, {{kind}}), layout?[блоки]}. "
        "Формула expr — выражение: числа, + - * / ^, скобки; имена: группы точек (all, volume, control, свои), "
        "setpoint, display (показание индикатора ИО — внесёт человек), n (число всех отсчётов), repeats, "
        "параметры режима, ключи характеристик выше; функции: " + expr.functions_help() + ". "
        "Группа в арифметике — все её отсчёты; ряд с числом — поэлементно. "
        "Готовые формулы (можно указать preset вместо expr): " + presets + ". " + layout.blocks_help()
    )

