"""Удалили строку письма (любым путём: sync, действие, админка) — удалить и исходник .eml."""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.mailbox.models import Message


@receiver(post_delete, sender=Message)
def drop_raw(sender, instance: Message, **kwargs) -> None:
    if not instance.raw:
        return
    storage, name = instance.raw.storage, instance.raw.name
    transaction.on_commit(lambda: storage.delete(name) if storage.exists(name) else None)
