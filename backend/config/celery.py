import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("ei_doc")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Повторяющиеся задачи (apps.tasks.recurrence): раз в день в 06:00 по местному
# времени (CELERY_TIMEZONE = Europe/Kirov, см. settings/base.py) заводятся
# очередные экземпляры, чей срок по cron уже наступил.
app.conf.beat_schedule = {
    "tasks-generate-recurring": {
        "task": "apps.tasks.tasks.generate_recurring_tasks",
        "schedule": crontab(hour=6, minute=0),
    },
    # Почта и уведомления (apps.notify): подстраховка очереди — если в момент события
    # Redis был недоступен, письма и сводки всё равно уйдут.
    "notify-sweep": {"task": "apps.notify.tasks.sweep", "schedule": crontab(minute="*/2")},
    # Сроки задач — каждому его задачи на сегодня и просроченные.
    "notify-tasks-due": {"task": "apps.notify.tasks.remind_tasks_due", "schedule": crontab(hour=7, minute=40)},
    # Метрологам — сколько поверок ждёт нормоконтроля (по будням).
    "notify-review-queue": {
        "task": "apps.notify.tasks.remind_review_queue", "schedule": crontab(hour=8, minute=10, day_of_week="1-5"),
    },
}
