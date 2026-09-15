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
}
