"""Очередь писем, события для сводок и настройки уведомлений сотрудника."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Category(models.TextChoices):
    """Что можно выключить у себя в профиле. Письма о пароле не выключаются."""

    REQUESTS = "requests", "Новые заявки с сайта"
    ASSIGNMENTS = "assignments", "Мне назначили выезд, заявку, приёмку или задачу"
    REVIEW = "review", "Нормоконтроль: возвраты на доработку и очередь на проверку"
    REMINDERS = "reminders", "Напоминания о сроках задач"
    SECURITY = "security", "Пароль и вход"


OPTIONAL_CATEGORIES = [c for c in Category if c != Category.SECURITY]


class NotifyPrefs(models.Model):
    """Что человек хочет получать на почту. Нет строки — всё включено."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notify_prefs",
        verbose_name="пользователь",
    )
    requests = models.BooleanField(Category.REQUESTS.label, default=True)
    assignments = models.BooleanField(Category.ASSIGNMENTS.label, default=True)
    review = models.BooleanField(Category.REVIEW.label, default=True)
    reminders = models.BooleanField(Category.REMINDERS.label, default=True)

    class Meta:
        verbose_name = "настройки уведомлений"
        verbose_name_plural = "настройки уведомлений"

    def __str__(self) -> str:
        return str(self.user)

    @staticmethod
    def wants_email(user, category: str) -> bool:
        if category == Category.SECURITY:
            return True
        prefs = NotifyPrefs.objects.filter(user=user).first()
        return True if prefs is None else bool(getattr(prefs, category, True))


class AccountSecurity(models.Model):
    """Когда у человека в последний раз менялся пароль — чтобы закрыть входы,
    выданные до этого и не несущие метки пароля (токены старых версий)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_security",
        verbose_name="пользователь",
    )
    password_changed_at = models.DateTimeField("пароль сменён", null=True, blank=True)

    class Meta:
        verbose_name = "безопасность входа"
        verbose_name_plural = "безопасность входа"

    def __str__(self) -> str:
        return str(self.user)


class NotifyEvent(models.Model):
    """Одно событие для человека. Сворачиваются в письмо — apps.notify.events.flush."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notify_events",
        verbose_name="кому",
    )
    category = models.CharField("категория", max_length=12, choices=Category.choices)
    kind = models.CharField("событие", max_length=32)
    object_id = models.BigIntegerField("объект", null=True, blank=True)
    payload = models.JSONField("данные", default=dict, blank=True)
    actor_id = models.BigIntegerField("кто сделал", null=True, blank=True)
    created_at = models.DateTimeField("когда", auto_now_add=True)
    dispatched_at = models.DateTimeField("разослано", null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "событие уведомления"
        verbose_name_plural = "события уведомлений"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.kind} → {self.user}"


class OutboxStatus(models.TextChoices):
    PENDING = "pending", "В очереди"
    SENT = "sent", "Отправлено"
    FAILED = "failed", "Не отправлено"


class OutboxMail(models.Model):
    """Письмо в очереди. Уходит фоном с повторами (apps.notify.outbox.deliver)."""

    to = models.EmailField("кому")
    subject = models.CharField("тема", max_length=255)
    text = models.TextField("текст")
    html = models.TextField("HTML", blank=True)
    template = models.CharField("шаблон", max_length=40, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="outbox_mails", verbose_name="пользователь",
    )
    status = models.CharField("состояние", max_length=8, choices=OutboxStatus.choices, default=OutboxStatus.PENDING)
    attempts = models.PositiveSmallIntegerField("попыток", default=0)
    next_attempt_at = models.DateTimeField("следующая попытка", null=True, blank=True)
    last_error = models.TextField("последняя ошибка", blank=True)
    created_at = models.DateTimeField("поставлено", auto_now_add=True)
    sent_at = models.DateTimeField("отправлено", null=True, blank=True)

    class Meta:
        verbose_name = "письмо"
        verbose_name_plural = "очередь писем"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="outbox_status_next")]

    def __str__(self) -> str:
        return f"{self.subject} → {self.to}"
