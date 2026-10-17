"""Формулы аттестации как выражения — пишет ассистент (или метролог), считает EI_doc.

Методики аттестации у разных видов оборудования считают по-разному, поэтому
закрытого списка формул нет. Формула — строка вида

    mean(control) - max(means(volume))
    (max(control) - min(control)) / 2
    max(abs(all - setpoint))
    round(mean(all), 4) - setpoint

Код от модели не выполняется: строка разбирается через ``ast`` и считается
своим интерпретатором с белым списком узлов и функций, арифметика — Decimal.

Типы значений:

* число (Decimal);
* ряд (``Vec``) — отсчёты одной точки или любой набор чисел, арифметика
  поэлементная, число с рядом — к каждому элементу;
* группа точек (``Group``) — ``all``, ``volume``, ``control`` или своя группа
  из плана. В арифметике и в mean/min/max/… группа разворачивается во все
  отсчёты своих точек; ``means(группа)`` — ряд средних по точкам.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_UP,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    localcontext,
)

MAX_LENGTH = 600
MAX_DEPTH = 40


class ExprError(ValueError):
    """Формула не разобрана или не считается — сообщение для человека."""


class Missing(ExprError):
    """Не хватает данных (например, не внесено показание индикатора)."""


@dataclass(frozen=True)
class Vec:
    values: tuple[Decimal, ...]


@dataclass(frozen=True)
class Group:
    name: str
    points: tuple[tuple[str, Vec], ...]  # (ключ точки, отсчёты)

    def flat(self) -> Vec:
        return Vec(tuple(v for _, vec in self.points for v in vec.values))


# ---------------------------------------------------------------------------
# Приведение типов
# ---------------------------------------------------------------------------
def _vec(value, fn: str) -> Vec:
    if isinstance(value, Group):
        return value.flat()
    if isinstance(value, Vec):
        return value
    if isinstance(value, Decimal):
        return Vec((value,))
    raise ExprError(f"{fn}: нужен ряд чисел или группа точек")


def _nonempty(vec: Vec, fn: str) -> tuple[Decimal, ...]:
    if not vec.values:
        raise ExprError(f"{fn}: пустой набор значений (в группе нет точек?)")
    return vec.values


def _num(value, fn: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    raise ExprError(f"{fn}: нужно одно число, а не ряд — оберните в mean/min/max/…")


def _arith(value):
    return value.flat() if isinstance(value, Group) else value


def _elementwise(a, b, op):
    a, b = _arith(a), _arith(b)
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return op(a, b)
    if isinstance(a, Vec) and isinstance(b, Vec):
        if len(a.values) != len(b.values):
            raise ExprError(f"ряды разной длины ({len(a.values)} и {len(b.values)})")
        return Vec(tuple(op(x, y) for x, y in zip(a.values, b.values)))
    if isinstance(a, Vec):
        return Vec(tuple(op(x, b) for x in a.values))
    if isinstance(b, Vec):
        return Vec(tuple(op(a, y) for y in b.values))
    raise ExprError("арифметика над неподдерживаемым значением")


def _map(value, fn):
    value = _arith(value)
    if isinstance(value, Vec):
        return Vec(tuple(fn(v) for v in value.values))
    return fn(value)


def _div(a: Decimal, b: Decimal) -> Decimal:
    if b == 0:
        raise ExprError("деление на ноль")
    return a / b


def _pow(a: Decimal, b: Decimal) -> Decimal:
    if abs(b) > 64:
        raise ExprError("показатель степени больше 64 — проверьте формулу")
    try:
        return a ** b
    except (InvalidOperation, DivisionByZero) as exc:
        raise ExprError(f"нельзя возвести {a} в степень {b}") from exc


def _sqrt(a: Decimal) -> Decimal:
    if a < 0:
        raise ExprError("корень из отрицательного числа")
    return a.sqrt()


# ---------------------------------------------------------------------------
# Функции
# ---------------------------------------------------------------------------
def f_mean(x):
    values = _nonempty(_vec(x, "mean"), "mean")
    return sum(values, Decimal(0)) / len(values)


def f_sum(x):
    return sum(_vec(x, "sum").values, Decimal(0))


def f_count(x):
    return Decimal(len(_vec(x, "count").values))


def _minmax(name, pick):
    def fn(*args):
        if len(args) == 1:
            return pick(_nonempty(_vec(args[0], name), name))
        values = []
        for a in args:
            values.extend(_vec(a, name).values)
        return pick(_nonempty(Vec(tuple(values)), name))

    return fn


def f_range(x):
    values = _nonempty(_vec(x, "range"), "range")
    return max(values) - min(values)


def f_std(x):
    """Выборочное СКО (n − 1)."""
    values = _nonempty(_vec(x, "std"), "std")
    if len(values) < 2:
        raise ExprError("std: нужно хотя бы два значения")
    m = sum(values, Decimal(0)) / len(values)
    return (sum(((v - m) ** 2 for v in values), Decimal(0)) / (len(values) - 1)).sqrt()


def f_means(g):
    if isinstance(g, Group):
        if not g.points:
            raise ExprError(f"means: в группе «{g.name}» нет точек")
        return Vec(tuple(f_mean(vec) for _, vec in g.points))
    raise ExprError("means: аргумент — группа точек (all, volume, control или своя)")


def f_abs(x):
    return _map(x, abs)


def f_sqrt(x):
    return _map(x, _sqrt)


def f_round(x, digits=Decimal(0)):
    d = _num(digits, "round")
    if d != d.to_integral_value() or not 0 <= d <= 10:
        raise ExprError("round: второй аргумент — целое число знаков от 0 до 10")
    q = Decimal(1).scaleb(-int(d))
    return _map(x, lambda v: v.quantize(q, rounding=ROUND_HALF_UP))


FUNCTIONS = {
    "mean": (f_mean, 1, 1, "среднее"),
    "min": (_minmax("min", min), 1, 20, "наименьшее"),
    "max": (_minmax("max", max), 1, 20, "наибольшее"),
    "sum": (f_sum, 1, 1, "сумма"),
    "count": (f_count, 1, 1, "число значений"),
    "range": (f_range, 1, 1, "размах max − min"),
    "std": (f_std, 1, 1, "выборочное СКО (n − 1)"),
    "means": (f_means, 1, 1, "ряд средних по точкам группы"),
    "abs": (f_abs, 1, 1, "модуль (поэлементно)"),
    "sqrt": (f_sqrt, 1, 1, "корень (поэлементно)"),
    "round": (f_round, 1, 2, "округление до n знаков, как ROUND в Excel"),
}
# point("7") — отсчёты одной точки; разбирается отдельно (строковый аргумент)


def functions_help() -> str:
    return "; ".join(f"{name} — {desc}" for name, (_, _, _, desc) in FUNCTIONS.items()) + \
        '; point("ключ") — отсчёты одной точки'


# ---------------------------------------------------------------------------
# Разбор
# ---------------------------------------------------------------------------
_BINOPS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: _div, ast.Pow: _pow,
}


def normalize(source: str) -> str:
    text = str(source or "").strip()
    # «^» — степень, как в Excel. В Python это XOR с другим приоритетом, поэтому заменяем до разбора.
    for a, b in (("−", "-"), ("–", "-"), ("×", "*"), ("·", "*"), ("÷", "/"), ("Σ", "sum"), ("^", "**")):
        text = text.replace(a, b)
    return text


def parse(source: str) -> ast.Expression:
    text = normalize(source)
    if not text:
        raise ExprError("пустая формула")
    if len(text) > MAX_LENGTH:
        raise ExprError(f"формула длиннее {MAX_LENGTH} символов")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"не разобрана формула «{text}»: проверьте скобки и знаки (десятичная точка, не запятая)") from exc
    _check(tree.body, 0)
    return tree


def _check(node, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise ExprError("слишком глубокая вложенность формулы")
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ExprError("допустимы только + − * / ^")
        _check(node.left, depth + 1)
        _check(node.right, depth + 1)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.USub, ast.UAdd)):
            raise ExprError("допустим только унарный минус")
        _check(node.operand, depth + 1)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ExprError("вызов функции — только по имени и без именованных аргументов")
        name = node.func.id
        if name == "point":
            if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                raise ExprError('point("ключ точки") — один аргумент в кавычках')
            return
        if name not in FUNCTIONS:
            raise ExprError(f"неизвестная функция {name}. Есть: {', '.join(FUNCTIONS)}, point")
        _, lo, hi, _ = FUNCTIONS[name]
        if not lo <= len(node.args) <= hi:
            raise ExprError(f"{name}: аргументов должно быть от {lo} до {hi}")
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                raise ExprError("распаковка аргументов не поддерживается")
            _check(arg, depth + 1)
    elif isinstance(node, ast.Name):
        if node.id in FUNCTIONS or node.id == "point":
            raise ExprError(f"{node.id} — функция, нужны скобки: {node.id}(…)")
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExprError("в формуле допустимы только числа")
    else:
        raise ExprError(f"недопустимая конструкция в формуле: {type(node).__name__}")


def names(source: str) -> set[str]:
    """Имена переменных в формуле — для проверки плана."""
    return {n.id for n in ast.walk(parse(source)) if isinstance(n, ast.Name) and n.id not in FUNCTIONS and n.id != "point"}


def point_refs(source: str) -> set[str]:
    return {
        n.args[0].value for n in ast.walk(parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "point"
    }


# ---------------------------------------------------------------------------
# Вычисление
# ---------------------------------------------------------------------------
def evaluate(source: str, env: dict, points: dict[str, Vec] | None = None):
    """Посчитать формулу. env — имя → Decimal | Vec | Group; points — для point("…")."""
    tree = parse(source)
    with localcontext() as ctx:
        ctx.prec = 34
        try:
            return _eval(tree.body, env, points or {})
        except (InvalidOperation, DivisionByZero) as exc:
            raise ExprError(f"формула не считается: {exc.__class__.__name__}") from exc


def evaluate_number(source: str, env: dict, points: dict[str, Vec] | None = None) -> Decimal:
    value = evaluate(source, env, points)
    if isinstance(value, (Vec, Group)):
        raise ExprError("формула даёт ряд, а нужно одно число — оберните в mean/min/max/…")
    return value


def _eval(node, env: dict, points: dict):
    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ExprError(f"неизвестное имя «{node.id}»")
        value = env[node.id]
        if value is None:
            raise Missing(f"нет значения «{node.id}»")
        return value
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, env, points)
        return _map(value, lambda v: -v) if isinstance(node.op, ast.USub) else _arith(value)
    if isinstance(node, ast.BinOp):
        return _elementwise(_eval(node.left, env, points), _eval(node.right, env, points), _BINOPS[type(node.op)])
    if isinstance(node, ast.Call):
        name = node.func.id
        if name == "point":
            key = node.args[0].value
            if key not in points:
                raise ExprError(f'point("{key}"): нет такой точки')
            return points[key]
        fn = FUNCTIONS[name][0]
        return fn(*[_eval(a, env, points) for a in node.args])
    raise ExprError("недопустимая конструкция")
