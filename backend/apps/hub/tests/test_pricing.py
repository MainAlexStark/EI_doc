"""Расчёт примерной цены заявки — apps.hub.pricing."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import MeasurementFamily, PricingSettings
from apps.hub.pricing import estimate


class EstimateTestCase(TestCase):
    def setUp(self) -> None:
        self.water = MeasurementFamily.objects.create(
            code="water-meter", name="Счётчики воды", price=Decimal("500.00")
        )
        self.scales = MeasurementFamily.objects.create(
            code="scales", name="Весы", price=Decimal("1200.00")
        )

    def test_subtotal_sums_unit_price_by_quantity(self):
        result = estimate([(self.water, 2), (self.scales, 1)], is_priority_slot=False)
        assert result["subtotal"] == Decimal("2200.00")
        assert result["discount_percent"] == Decimal("0")
        assert result["discount_amount"] == Decimal("0.00")
        assert result["total"] == Decimal("2200.00")

    def test_priority_slot_applies_the_configured_discount(self):
        PricingSettings.objects.create(priority_discount_percent=Decimal("15.0"))
        result = estimate([(self.water, 2)], is_priority_slot=True)
        assert result["discount_percent"] == Decimal("15.0")
        assert result["discount_amount"] == Decimal("150.00")
        assert result["total"] == Decimal("850.00")

    def test_default_pricing_settings_is_created_lazily(self):
        assert PricingSettings.objects.count() == 0
        result = estimate([(self.water, 1)], is_priority_slot=True)
        assert PricingSettings.objects.count() == 1
        assert result["discount_percent"] == Decimal("10")

    def test_no_items_is_zero(self):
        result = estimate([], is_priority_slot=True)
        assert result["subtotal"] == Decimal("0")
        assert result["total"] == Decimal("0")
