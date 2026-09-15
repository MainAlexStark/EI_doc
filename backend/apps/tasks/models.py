"""Задачи.

Одна сущность на всё: дерево (родитель агрегирует прогресс детей), generic FK
на любую сущность (наряд, поверку, аттестацию, эталон) — вместо отдельной
модели «задача для X» на каждый случай. Повторяющиеся задачи (поверка
эталонов, отчётность, аттестации) заводятся по cron — см. apps.tasks.recurrence.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class TaskStatus(models.TextChoices):
    TODO = "todo", "К выполнению"
    IN_PROGRESS = "in_progress", "В работе"
    DONE = "done", "Выполнена"
    CANCELLED = "cancelled", "Отменена"


class TaskPriority(models.TextChoices):
    LOW = "low", "Низкий"
    NORMAL = "normal", "Обычный"
    HIGH = "high", "Высокий"
    URGENT = "urgent", "Срочно"


class Task(models.Model):
    title = models.CharField("что сделать", max_length=250)
    description = models.TextField("описание", blank=True)

    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="children", verbose_name="родительская задача",
    )
    assignee = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks", verbose_name="исполнитель",
    )
    created_by = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_tasks", verbose_name="постановщик",
    )

    status = models.CharField("статус", max_length=12, choices=TaskStatus.choices, default=TaskStatus.TODO)
    priority = models.CharField(
        "приоритет", max_length=8, choices=TaskPriority.choices, default=TaskPriority.NORMAL
    )
    due_date = models.DateField("срок", null=True, blank=True, db_index=True)
    estimated_hours = models.DecimalField(
        "оценка, ч", max_digits=5, decimal_places=1, null=True, blank=True,
        help_text="Для загрузки сотрудников на неделю — без оценки задача в неё не попадает",
    )

    # Привязка к любой сущности вместо отдельной модели на каждый случай.
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True, verbose_name="тип объекта"
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    linked_object = GenericForeignKey("content_type", "object_id")

    recurrence = models.CharField(
        "повтор (cron)", max_length=60, blank=True,
        help_text="Стандартное cron-выражение, например «0 6 1 * *» — раз в месяц. "
                  "Пусто — разовая задача. См. apps.tasks.recurrence",
    )
    recurrence_parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="recurrence_instances", verbose_name="повторяется из",
        help_text="Заполняется у задач, порождённых по расписанию",
    )

    created_at = models.DateTimeField("создана", auto_now_add=True)
    updated_at = models.DateTimeField("изменена", auto_now=True)
    completed_at = models.DateTimeField("выполнена", null=True, blank=True)

    class Meta:
        verbose_name = "задача"
        verbose_name_plural = "задачи"
        ordering = ["due_date", "-priority", "id"]
        indexes = [
            models.Index(fields=["assignee", "status"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def progress(self) -> int:
        """Доля выполненных подзадач, 0-100. Лист без детей — 100 или 0 по своему статусу."""
        children = list(self.children.all())
        if not children:
            return 100 if self.status == TaskStatus.DONE else 0
        done = sum(1 for child in children if child.status == TaskStatus.DONE)
        return round(done / len(children) * 100)

    def save(self, *args, **kwargs):
        if self.status == TaskStatus.DONE and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != TaskStatus.DONE:
            self.completed_at = None
        super().save(*args, **kwargs)
