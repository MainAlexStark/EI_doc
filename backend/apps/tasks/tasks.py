"""Celery-обёртка над apps.tasks.recurrence — расписание в config/celery.py."""

from __future__ import annotations

from celery import shared_task

from apps.tasks.recurrence import generate_due


@shared_task
def generate_recurring_tasks() -> int:
    return len(generate_due())
