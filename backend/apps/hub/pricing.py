"""Расчёт примерной цены заявки.

Отдельный модуль от api.py: серверный пересчёт цены — правило домена ("не
верь клиенту"), а не деталь HTTP-обработки. Фронтенд считает то же самое на
лету, чтобы цена на форме обновлялась без похода на сервер при каждом клике,
но в базу всегда попадает снимок, посчитанный здесь.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from apps.catalog.models import MeasurementFamily, PricingSettings


def estimate(items: list[tuple[MeasurementFamily, int]], *, is_priority_slot: bool) -> dict:
    """items — [(family, quantity), ...] с MeasurementFamily.price, актуальным на сейчас."""
    subtotal = sum((family.price * quantity for family, quantity in items), Decimal("0"))
    discount_percent = (
        PricingSettings.current().priority_discount_percent if is_priority_slot else Decimal("0")
    )
    discount_amount = (subtotal * discount_percent / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return {
        "subtotal": subtotal,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "total": subtotal - discount_amount,
    }
