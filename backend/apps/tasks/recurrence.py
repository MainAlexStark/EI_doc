"""Повторяющиеся задачи: поверка эталонов, отчётность, аттестации.

Шаблон — задача с непустым `recurrence` и без `recurrence_parent` (сама не
порождена по расписанию). Экземпляры — обычные задачи с `recurrence_parent`,
указывающим на шаблон; сами они не повторяются, чтобы не плодить деревья.

Запускается раз в день командой generate_recurring_tasks (Celery beat,
см. config/celery.py). За один прогон шаблон получает не больше одного
нового экземпляра — если демон стоял неделю, задача добежит на следующий
день, а не выдаст семь просроченных разом.
"""

from __future__ import annotations

import datetime as dt

from croniter import croniter
from django.utils import timezone

from apps.tasks.models import Task


def generate_due(*, today: dt.date | None = None) -> list[Task]:
    today = today or timezone.localdate()
    created: list[Task] = []

    templates = Task.objects.filter(recurrence_parent__isnull=True).exclude(recurrence="")
    for template in templates:
        last = template.recurrence_instances.order_by("-due_date").first()
        anchor_date = (last.due_date if last and last.due_date else template.due_date) or template.created_at.date()

        try:
            # dt.time.max, не .min: анкор — уже случившееся вхождение (сама
            # дата шаблона или дата последнего экземпляра). get_next() croniter
            # ищет строго ПОСЛЕ переданного момента, а не с него — если взять
            # начало дня, у cron-выражений с временем позже полуночи следующим
            # «вхождением» окажется тот же день. Конец дня отодвигает поиск на
            # следующий период.
            cron = croniter(template.recurrence, dt.datetime.combine(anchor_date, dt.time.max))
            next_run = cron.get_next(dt.datetime).date()
        except (ValueError, KeyError):
            continue  # некорректное cron-выражение — пропускаем, не роняем весь прогон

        if next_run > today:
            continue

        instance = Task.objects.create(
            title=template.title,
            description=template.description,
            assignee=template.assignee,
            created_by=template.created_by,
            priority=template.priority,
            estimated_hours=template.estimated_hours,
            content_type=template.content_type,
            object_id=template.object_id,
            due_date=next_run,
            recurrence_parent=template,
        )
        created.append(instance)

    return created
