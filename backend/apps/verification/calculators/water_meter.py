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

# Режимы измерений и длительность пролива, как в рабочих шаблонах.
MODE_MIN = "min"
MODE_TRANSITION = "transition"
MODE_MAX = "max"

MODES = {
    MODE_MIN: {"seconds": 720, "label": "Qнаим"},
    MODE_TRANSITION: {"seconds": 360, "label": "Qперех"},
    MODE_MAX: {"seconds": 120, "label": "Qнаиб"},
}

# Две раскладки протокола: короткая — по одному проливу на режим,
# полная — по три. Другой разницы между ними нет.
LAYOUT_COMPACT = "compact3"
LAYOUT_EXTENDED = "extended9"

LAYOUTS = {
    LAYOUT_COMPACT: [MODE_MIN, MODE_TRANSITION, MODE_MAX],
    LAYOUT_EXTENDED: [MODE_MIN] * 3 + [MODE_TRANSITION] * 3 + [MODE_MAX] * 3,
}

# Пункты протокола до таблицы измерений — их поверитель отмечает как есть.
CHECKS = {
    "visual": "Внешний осмотр (2.7.1)",
    "operation": "Опробование (2.7.2)",
    "tightness": "Проверка герметичности (2.7.2.1)",
}


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


class MeasurementError(ValueError):
    """Со строкой измерений что-то не так — показать поверителю, а не падать."""


@dataclass(frozen=True)
class Measurement:
    """Одна строка измерений — две величины и их сравнение.

    ``volume_meter`` — сколько намерил поверяемый счётчик. В протоколе это
    графа «Vсчет (Vij = K*N ij), м³». Задаётся, в порядке приоритета:

    1. напрямую;
    2. импульсами: ``pulses`` × ``pulse_weight`` (K, м³/имп);
    3. показаниями: ``reading_end`` − ``reading_start``.

    ``volume_standard`` — сколько воды прошло на самом деле, по эталонной
    установке. В протоколе это графа «Vэтал, м³». Только ввод: установка
    измеряет его сама, вывести его из расхода нельзя.

    Внимание на порядок: в старых шаблонах ячейка `AW` (Vсчет) считалась как
    Q × t / 3600, а `BD` (Vэтал) получалась прибавлением случайной поправки.
    Имена ячеек к смыслу колонок отношения не имеют — смотреть надо на
    заголовки `AW50` и `BD50` листа «Протокол».
    """

    flow_rate: Decimal            # Q, м³/ч — режим установки
    seconds: int                  # длительность пролива
    volume_standard: Decimal | None = None   # Vэтал — с установки
    volume_meter: Decimal | None = None      # Vсчет
    reading_start: Decimal | None = None
    reading_end: Decimal | None = None
    pulses: int | None = None
    pulse_weight: Decimal | None = None      # K, м³/имп
    mode: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "volume_meter", self._resolve_volume_meter())
        if self.volume_standard is None:
            raise MeasurementError(
                "Не указан объём по эталону — его показывает поверочная установка, "
                "рассчитать его из расхода нельзя"
            )
        object.__setattr__(self, "volume_standard", _dec(self.volume_standard))

    def _resolve_volume_meter(self) -> Decimal:
        if self.volume_meter is not None:
            return _dec(self.volume_meter)

        if self.pulses is not None:
            if self.pulse_weight is None:
                raise MeasurementError(
                    "Указаны импульсы, но не задан коэффициент преобразования счётчика"
                )
            return _dec(self.pulses) * _dec(self.pulse_weight)

        if self.reading_start is not None and self.reading_end is not None:
            volume = _dec(self.reading_end) - _dec(self.reading_start)
            if volume < 0:
                raise MeasurementError(
                    f"Показания в конце ({self.reading_end}) меньше, чем в начале "
                    f"({self.reading_start})"
                )
            return volume

        raise MeasurementError(
            "Нечем посчитать объём по счётчику: нужны либо показания начала и конца, "
            "либо импульсы с коэффициентом, либо объём напрямую"
        )

    @property
    def expected_volume(self) -> Decimal:
        """Сколько воды должно было пройти за пролив: ROUNDUP(Q × t / 3600, 3).

        Не результат измерения, а ориентир: помогает поймать опечатку в
        объёме по эталону или в длительности.
        """
        return excel_roundup(_dec(self.flow_rate) * self.seconds / Decimal(3600), 3)

    @property
    def standard_deviation_pct(self) -> Decimal:
        """На сколько объём по эталону разошёлся с расчётным, %."""
        expected = self.expected_volume
        if expected == 0:
            return Decimal(0)
        return excel_round((_dec(self.volume_standard) - expected) / expected * 100, 1)

    @property
    def relative_error(self) -> Decimal:
        """δ = ROUND((Vсчет − Vэтал) / Vэтал × 100, 1), %."""
        volume_standard = _dec(self.volume_standard)
        if volume_standard == 0:
            raise MeasurementError(
                "Объём по эталону равен нулю — через установку не прошло воды, "
                "погрешность не определена"
            )
        ratio = (_dec(self.volume_meter) - volume_standard) / volume_standard * 100
        return excel_round(ratio, 1)

    def is_within_limits(self, limits: MeterLimits, meter_class: str) -> bool:
        return abs(self.relative_error) <= limits.error_limit(self.flow_rate, meter_class)

    def as_result(self, limits: MeterLimits, meter_class: str) -> dict:
        """Строка таблицы результатов — то, что уйдёт в протокол."""
        return {
            "mode": self.mode,
            "seconds": self.seconds,
            "flow_rate": str(_dec(self.flow_rate)),
            "reading_start": str(_dec(self.reading_start)) if self.reading_start is not None else None,
            "reading_end": str(_dec(self.reading_end)) if self.reading_end is not None else None,
            "volume_meter": str(_dec(self.volume_meter)),
            "volume_standard": str(_dec(self.volume_standard)),
            "expected_volume": str(self.expected_volume),
            "error_pct": str(self.relative_error),
            "limit_pct": str(limits.error_limit(self.flow_rate, meter_class)),
            "within_limits": self.is_within_limits(limits, meter_class),
        }


@dataclass(frozen=True)
class Verdict:
    suitable: bool
    failed_rows: tuple[int, ...]
    max_flow_rate: Decimal
    reasons: tuple[str, ...] = ()


def evaluate(
    measurements: list[Measurement],
    limits: MeterLimits,
    meter_class: str,
    *,
    checks: dict[str, bool] | None = None,
) -> Verdict:
    """Годен или нет.

    Счётчик годен, когда пройдены внешний осмотр, опробование и проверка
    герметичности И погрешность на каждом режиме в пределах допуска.
    Вердикт выводится из данных — отдельной «галочки годности» нет.
    """
    if not measurements:
        raise MeasurementError("Нет строк измерений")

    reasons = []
    for key, label in CHECKS.items():
        if checks is not None and checks.get(key) is False:
            reasons.append(f"{label}: не соответствует")

    failed = []
    for index, measurement in enumerate(measurements, start=1):
        if not measurement.is_within_limits(limits, meter_class):
            failed.append(index)
            reasons.append(
                f"строка {index}: δ = {measurement.relative_error} % при допуске "
                f"± {limits.error_limit(measurement.flow_rate, meter_class)} % "
                f"на расходе {measurement.flow_rate} м³/ч"
            )

    return Verdict(
        suitable=not reasons,
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
