"""Сигналы, которые не относятся ни к вводу измерений, ни к нумерации.

Отдельный файл, а не хук в apps.verification.api или numbering: это не часть
их логики, а побочный эффект сохранения поверки — закрытие наряда.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.verification.models import Verification


@receiver(post_save, sender=Verification)
def refresh_work_order_status(sender, instance: Verification, **kwargs) -> None:
    """Поверку приняли/переоткрыли — наряд может закрыться или откатиться из «закрыт»."""
    if instance.work_order_id:
        instance.work_order.refresh_status()
