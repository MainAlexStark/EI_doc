"""Условия поверки: журнал погоды и генерация недостающих значений.

Поведение старого приложения должно сохраниться в точности, иначе протоколы
за один день станут показывать разные условия в помещении.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

import pytest
from django.test import TestCase

from apps.catalog import conditions
from apps.catalog.models import AmbientRecord, ConditionProfile, MeasurementFamily
from apps.verification.models import ConditionSource

DAY = dt.date(2026, 3, 11)


class AmbientTestCase(TestCase):
    def test_first_call_generates_and_stores(self):
        ambient = conditions.ambient_for(DAY, rng=random.Random(1))

        assert ambient.source == ConditionSource.GENERATED
        record = AmbientRecord.objects.get(date=DAY)
        assert record.is_generated
        assert record.temperature == ambient.temperature

    def test_same_day_gives_same_values_to_every_protocol(self):
        """Главное свойство журнала погоды: один день — одни условия."""
        first = conditions.ambient_for(DAY, rng=random.Random(1))
        second = conditions.ambient_for(DAY, rng=random.Random(999))

        assert (second.temperature, second.pressure, second.humidity) == (
            first.temperature,
            first.pressure,
            first.humidity,
        )
        assert second.source == ConditionSource.ARCHIVE
        assert AmbientRecord.objects.count() == 1

    def test_generated_values_stay_inside_the_range(self):
        specs = conditions.ranges_for()
        for seed in range(30):
            AmbientRecord.objects.all().delete()
            ambient = conditions.ambient_for(DAY, rng=random.Random(seed))
            for field, key in (
                (ambient.temperature, "temperature"),
                (ambient.pressure, "pressure"),
                (ambient.humidity, "humidity"),
            ):
                assert Decimal(str(specs[key]["min"])) <= field <= Decimal(str(specs[key]["max"]))

    def test_measured_values_win_and_are_marked(self):
        conditions.record_measured(
            DAY, temperature=Decimal("21.4"), pressure=Decimal("99.1"), humidity=Decimal("48.0")
        )
        ambient = conditions.ambient_for(DAY)

        assert ambient.temperature == Decimal("21.4")
        assert ambient.source == ConditionSource.ARCHIVE
        assert AmbientRecord.objects.get(date=DAY).is_generated is False

    def test_existing_day_is_not_overwritten_silently(self):
        """Протоколы за этот день уже выпущены — менять условия задним числом нельзя."""
        conditions.ambient_for(DAY, rng=random.Random(1))
        before = AmbientRecord.objects.get(date=DAY).temperature

        conditions.record_measured(
            DAY, temperature=Decimal("30.0"), pressure=Decimal("90.0"), humidity=Decimal("40.0")
        )
        assert AmbientRecord.objects.get(date=DAY).temperature == before

        conditions.record_measured(
            DAY, temperature=Decimal("30.0"), pressure=Decimal("90.0"), humidity=Decimal("40.0"),
            overwrite=True,
        )
        assert AmbientRecord.objects.get(date=DAY).temperature == Decimal("30.0")

    def test_as_fields_maps_onto_verification(self):
        fields = conditions.ambient_for(DAY, rng=random.Random(1)).as_fields()
        assert set(fields) == {
            "temperature", "temperature_source",
            "pressure", "pressure_source",
            "humidity", "humidity_source",
        }


class WaterTemperatureTestCase(TestCase):
    def test_hot_and_cold_use_their_own_ranges(self):
        specs = conditions.ranges_for()
        for _ in range(50):
            hot = conditions.water_temperature(conditions.HOT)
            cold = conditions.water_temperature(conditions.COLD)
            assert specs["water_temperature_hot"]["min"] <= hot <= specs["water_temperature_hot"]["max"]
            assert specs["water_temperature_cold"]["min"] <= cold <= specs["water_temperature_cold"]["max"]

    def test_unknown_unit_type_is_rejected(self):
        with pytest.raises(ValueError, match="Тип счётчика"):
            conditions.water_temperature("тёплая")


class RangesTestCase(TestCase):
    def test_family_overrides_profile_which_overrides_defaults(self):
        ConditionProfile.objects.create(
            name="Основной", ranges={"temperature": {"min": 19.0, "max": 23.0}}
        )
        family = MeasurementFamily.objects.create(
            code="water", name="Счётчики воды", type_code="03",
            condition_ranges={"temperature": {"max": 22.0}},
        )

        specs = conditions.ranges_for(family)
        assert specs["temperature"]["min"] == 19.0   # из профиля
        assert specs["temperature"]["max"] == 22.0   # из семейства
        assert specs["pressure"] == conditions.DEFAULT_RANGES["pressure"]
