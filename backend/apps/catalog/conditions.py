"""Условия поверки: журнал погоды и генерация недостающих значений.

Поведение старого приложения сохранено целиком:

* значения на дату берутся из журнала погоды;
* если их там нет — генерируются случайно в нормативном диапазоне
  и **записываются в журнал**, поэтому все протоколы за один день
  показывают одни и те же условия;
* температура воды выбирается случайно по типу счётчика (г/в или х/в)
  и генерируется на каждый протокол отдельно.

Изменилось одно: вместе со значением теперь сохраняется его источник
(`ConditionSource`). Это внутреннее поле — в протокол не выводится,
нужно только нормоконтролю, чтобы отличить измеренное от подставленного.

Диапазоны лежат в базе (`ConditionProfile`), а не в config.yaml на машине
поверителя, и правятся метрологом через админку.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

# Нормальные условия применения СИ. ЗАМЕНИТЬ на значения из вашего config.yaml
# при первом запуске — это лишь безопасные значения по умолчанию.
DEFAULT_RANGES: dict[str, dict[str, float]] = {
    "temperature": {"min": 18.0, "max": 25.0, "decimals": 1},
    "pressure": {"min": 84.0, "max": 106.0, "decimals": 1},
    "humidity": {"min": 30.0, "max": 80.0, "decimals": 1},
    "water_temperature_cold": {"min": 5, "max": 20, "decimals": 0},
    "water_temperature_hot": {"min": 50, "max": 70, "decimals": 0},
}

HOT = "г/в"
COLD = "х/в"


@dataclass(frozen=True)
class Ambient:
    """Условия в помещении на дату, вместе с источником каждого значения."""

    temperature: Decimal
    pressure: Decimal
    humidity: Decimal
    source: str  # ConditionSource

    def as_fields(self) -> dict:
        """Поля для Verification."""
        return {
            "temperature": self.temperature,
            "temperature_source": self.source,
            "pressure": self.pressure,
            "pressure_source": self.source,
            "humidity": self.humidity,
            "humidity_source": self.source,
        }


def ranges_for(family=None) -> dict[str, dict[str, float]]:
    """Диапазоны: общий профиль, поверх него — переопределения семейства."""
    from apps.catalog.models import ConditionProfile

    merged = {key: dict(value) for key, value in DEFAULT_RANGES.items()}

    profile = ConditionProfile.objects.filter(is_active=True).first()
    if profile:
        for key, value in (profile.ranges or {}).items():
            merged.setdefault(key, {}).update(value)

    if family is not None:
        for key, value in (family.condition_ranges or {}).items():
            merged.setdefault(key, {}).update(value)

    return merged


def _draw(spec: dict[str, float], rng: random.Random) -> Decimal:
    decimals = int(spec.get("decimals", 1))
    value = Decimal(str(rng.uniform(float(spec["min"]), float(spec["max"]))))
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


@transaction.atomic
def ambient_for(
    date: dt.date, *, family=None, rng: random.Random | None = None
) -> Ambient:
    """Условия в помещении на дату.

    Есть запись в журнале погоды — берём её. Нет — генерируем в диапазоне
    и сразу записываем, чтобы следующий протокол за этот же день получил
    те же значения. Ровно как в старом приложении.
    """
    from apps.catalog.models import AmbientRecord
    from apps.verification.models import ConditionSource

    record = AmbientRecord.objects.select_for_update().filter(date=date).first()
    if record is not None:
        return Ambient(
            temperature=record.temperature,
            pressure=record.pressure,
            humidity=record.humidity,
            source=ConditionSource.ARCHIVE,
        )

    rng = rng or random.Random()
    specs = ranges_for(family)
    record = AmbientRecord.objects.create(
        date=date,
        temperature=_draw(specs["temperature"], rng),
        pressure=_draw(specs["pressure"], rng),
        humidity=_draw(specs["humidity"], rng),
        is_generated=True,
    )
    return Ambient(
        temperature=record.temperature,
        pressure=record.pressure,
        humidity=record.humidity,
        source=ConditionSource.GENERATED,
    )


def record_measured(
    date: dt.date, *, temperature, pressure, humidity, overwrite: bool = False
):
    """Занести в журнал погоды реально измеренные значения.

    По умолчанию не затирает уже существующую запись: если за этот день
    протоколы уже выпущены, менять условия задним числом нельзя.
    """
    from apps.catalog.models import AmbientRecord

    record, created = AmbientRecord.objects.get_or_create(
        date=date,
        defaults={
            "temperature": temperature,
            "pressure": pressure,
            "humidity": humidity,
            "is_generated": False,
        },
    )
    if not created and overwrite:
        record.temperature = temperature
        record.pressure = pressure
        record.humidity = humidity
        record.is_generated = False
        record.save(update_fields=["temperature", "pressure", "humidity", "is_generated"])
    return record


def water_temperature(unit_type: str, *, family=None, rng: random.Random | None = None) -> int:
    """Температура воды по типу счётчика. Разыгрывается на каждый протокол."""
    if unit_type not in (HOT, COLD):
        raise ValueError(f"Тип счётчика должен быть {HOT!r} или {COLD!r}, получено {unit_type!r}")

    rng = rng or random.Random()
    specs = ranges_for(family)
    key = "water_temperature_hot" if unit_type == HOT else "water_temperature_cold"
    spec = specs[key]
    return rng.randint(int(spec["min"]), int(spec["max"]))
