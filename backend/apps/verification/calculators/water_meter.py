"""Расчёт поверки счётчиков воды.

Формулы взяты из рабочих шаблонов `.xlsm` (лист «Протокол») и переписаны так,
чтобы давать те же числа, что Excel. Золотые тесты в
`apps/verification/tests/test_water_meter_golden.py` сверяют их с выпущенными
протоколами.

Что здесь считается:

    V_эталона = ROUNDUP(Q × t / 3600, 3)          м³
    δ         = ROUND((V_эталона − V_счётчика) / V_счётчика × 100, 1)   %
    показания_конца = показания_начала + V_эталона

Чего здесь НЕТ намеренно: в шаблонах сами величины Q, V_счётчика и показания
не измеряются, а генерируются функцией RANDBETWEEN. Эти формулы сюда не
перенесены — см. docs/measurements.md. Калькулятор считает по данным, которые
ему дали; откуда они берутся, решает тот, кто вносит поверку.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, ROUND_UP, Decimal, localcontext

CLASS_A = "А"
CLASS_B = "В"

# Режимы измерений и длительность пролива, как в шаблонах.
SECONDS_MIN = 720      # Qнаим
SECONDS_TRANSITION = 360   # Qперех
SECONDS_MAX = 120      # Qнаиб


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def excel_round(value, digits: int) -> Decimal:
    """ROUND() из Excel: половина округляется от нуля (не «к чётному»)."""
    with localcontext() as ctx:
        ctx.prec = 28
        return _dec(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def excel_roundup(value, digits: int) -> Decimal:
    """ROUNDUP() из Excel: всегда от нуля."""
    with localcontext() as ctx:
        ctx.prec = 28
        return _dec(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_UP)


@dataclass(frozen=True)
class MeterLimits:
    """Метрологические характеристики типа счётчика.

    Значения берутся из справочника типов (лист «Данные», таблица K54:W98
    старых шаблонов) и живут в catalog.SiType.attributes.
    """

    q_min: Decimal              # наименьший расход, м³/ч
    q_transition_a: Decimal     # переходный расход, класс А
    q_transition_b: Decimal     # переходный расход, класс В
    q_nominal: Decimal
    q_max: Decimal
    error_below_transition: Decimal   # ±%, ниже переходного расхода
    error_above_transition: Decimal   # ±%, от переходного и выше

    @classmethod
    def from_dict(cls, data: dict) -> "MeterLimits":
        return cls(
            q_min=_dec(data["q_min"]),
            q_transition_a=_dec(data["q_transition_a"]),
            q_transition_b=_dec(data["q_transition_b"]),
            q_nominal=_dec(data["q_nominal"]),
            q_max=_dec(data["q_max"]),
            error_below_transition=_dec(data["error_below_transition"]),
            error_above_transition=_dec(data["error_above_transition"]),
        )

    def transition(self, meter_class: str) -> Decimal:
        if meter_class not in (CLASS_A, CLASS_B):
            raise ValueError(f"Класс счётчика должен быть {CLASS_A!r} или {CLASS_B!r}")
        return self.q_transition_a if meter_class == CLASS_A else self.q_transition_b

    def range_start(self, meter_class: str) -> Decimal:
        """Нижняя граница диапазона: для класса А это 2×Qнаим."""
        return self.q_min * 2 if meter_class == CLASS_A else self.q_min

    def error_limit(self, flow_rate, meter_class: str) -> Decimal:
        """Предел допускаемой погрешности на этом расходе, %.

        Граница принадлежит верхнему поддиапазону: ровно на переходном
        расходе действует более жёсткий предел — так напечатано в протоколе
        («от 0,12 м3/ч до 3 м3/ч : ± 2 %»).
        """
        if _dec(flow_rate) < self.transition(meter_class):
            return self.error_below_transition
        return self.error_above_transition


@dataclass(frozen=True)
class Measurement:
    """Одна строка измерений."""

    flow_rate: Decimal        # Q, м³/ч
    seconds: int              # длительность пролива
    volume_meter: Decimal     # V по счётчику, м³
    reading_start: Decimal | None = None

    @property
    def volume_standard(self) -> Decimal:
        """V эталона: ROUNDUP(Q × t / 3600, 3)."""
        return excel_roundup(_dec(self.flow_rate) * self.seconds / Decimal(3600), 3)

    @property
    def relative_error(self) -> Decimal:
        """δ = ROUND((Vэт − Vсч) / Vсч × 100, 1), %."""
        volume_meter = _dec(self.volume_meter)
        if volume_meter == 0:
            raise ZeroDivisionError("Объём по счётчику равен нулю — погрешность не определена")
        ratio = (self.volume_standard - volume_meter) / volume_meter * 100
        return excel_round(ratio, 1)

    @property
    def reading_end(self) -> Decimal | None:
        if self.reading_start is None:
            return None
        return _dec(self.reading_start) + self.volume_standard

    def is_within_limits(self, limits: MeterLimits, meter_class: str) -> bool:
        return abs(self.relative_error) <= limits.error_limit(self.flow_rate, meter_class)


@dataclass(frozen=True)
class Verdict:
    suitable: bool
    failed_rows: tuple[int, ...]
    max_flow_rate: Decimal
    reasons: tuple[str, ...] = ()


def evaluate(
    measurements: list[Measurement], limits: MeterLimits, meter_class: str
) -> Verdict:
    """Годен или нет: погрешность на каждом режиме в пределах допуска."""
    if not measurements:
        raise ValueError("Нет строк измерений")

    failed = []
    reasons = []
    for index, measurement in enumerate(measurements, start=1):
        if not measurement.is_within_limits(limits, meter_class):
            failed.append(index)
            reasons.append(
                f"строка {index}: δ = {measurement.relative_error} % при допуске "
                f"± {limits.error_limit(measurement.flow_rate, meter_class)} % "
                f"на расходе {measurement.flow_rate} м³/ч"
            )

    return Verdict(
        suitable=not failed,
        failed_rows=tuple(failed),
        max_flow_rate=max(_dec(m.flow_rate) for m in measurements),
        reasons=tuple(reasons),
    )


def flow_range_note(measurements: list[Measurement], limits: MeterLimits) -> str:
    """Строка для графы «Прочие сведения» журнала.

    В журнале это выглядит так:
    «Поверен в диапазоне расхода (0,03-0.948) м3/ч»
    """
    q_max = max(_dec(m.flow_rate) for m in measurements)
    low = f"{limits.q_min.normalize():f}".replace(".", ",")
    return f"Поверен в диапазоне расхода ({low}-{q_max.normalize():f}) м3/ч"
