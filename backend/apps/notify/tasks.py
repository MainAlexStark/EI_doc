"""Фоновые задачи почты и уведомлений (Celery)."""

from __future__ import annotations

from celery import shared_task


@shared_task(ignore_result=True)
def deliver_outbox() -> int:
    from apps.notify.outbox import deliver

    total = 0
    while True:  # пачками по 20, пока есть готовые
        sent = deliver()
        total += sent
        if sent == 0:
            return total


@shared_task(ignore_result=True)
def flush_events() -> int:
    from apps.notify.events import flush

    return flush()


@shared_task(ignore_result=True)
def sweep() -> None:
    """Подстраховка раз в пару минут: брокер мог быть недоступен в момент события."""
    from django.conf import settings

    from apps.notify.events import flush
    from apps.notify.outbox import deliver

    flush(older_than=getattr(settings, "NOTIFY_FLUSH_DELAY", 20) * 2)
    while deliver():
        pass


@shared_task(ignore_result=True)
def remind_tasks_due() -> int:
    """07:40 — каждому исполнителю его задачи со сроком сегодня и просроченные."""
    from django.utils import timezone

    from apps.notify.events import record, user_of
    from apps.notify.models import Category
    from apps.tasks.models import Task, TaskStatus

    today = timezone.localdate()
    n = 0
    for task in (
        Task.objects.filter(due_date__lte=today, assignee__isnull=False)
        .exclude(status__in=[TaskStatus.DONE, TaskStatus.CANCELLED])
        .select_related("assignee__user")
    ):
        user = user_of(task.assignee)
        if user is not None:
            record(user, Category.REMINDERS, "task_due", task.pk, skip_actor=False)
            n += 1
    return n


@shared_task(ignore_result=True)
def remind_review_queue() -> int:
    """08:10 — метрологам: сколько поверок ждёт нормоконтроля (поштучно не пишем — их сотни)."""
    from apps.core.models import Role
    from apps.notify.events import record, users_with_roles
    from apps.notify.models import Category
    from apps.verification.models import Verification, VerificationStatus

    count = Verification.objects.filter(status=VerificationStatus.READY).count()
    if not count:
        return 0
    users = users_with_roles([Role.METROLOGIST])
    for user in users:
        record(user, Category.REVIEW, "review_queue", None, skip_actor=False, count=count)
    return len(users)
