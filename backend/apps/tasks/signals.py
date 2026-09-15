"""Уведомление исполнителя о новой задаче — отдельно от CRUD в api.py."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.tasks.models import Task


@receiver(post_save, sender=Task)
def notify_assignee(sender, instance: Task, created: bool, **kwargs) -> None:
    if not created or not instance.assignee_id:
        return
    from apps.core.telegram import send_message

    chat_id = instance.assignee.telegram_chat_id
    if not chat_id:
        return
    when = f" к {instance.due_date:%d.%m.%Y}" if instance.due_date else ""
    send_message(chat_id, f"Новая задача: {instance.title}{when}")
