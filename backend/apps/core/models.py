"""Пользователи, сотрудники, аттестации.

Ключевая мысль: право подписи протокола — это не роль и не галочка в интерфейсе,
а доменное правило (действующая аттестация поверителя на данное семейство СИ).
Проверка живёт здесь и срабатывает одинаково из веб-формы, из синхронизации
с телефона и из админки.
"""

from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Role(models.TextChoices):
    OWNER = "owner", "Владелец"
    HEAD = "head", "Руководитель"
    METROLOGIST = "metrologist", "Метролог / нормоконтроль"
    MANAGER = "manager", "Менеджер"
    VERIFIER = "verifier", "Поверитель"
    OBSERVER = "observer", "Наблюдатель"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Нужен email")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", Role.OWNER)
        return self._create(email, password, **extra)


class User(AbstractUser):
    username = None
    email = models.EmailField("email", unique=True)
    role = models.CharField("роль", max_length=20, choices=Role.choices, default=Role.OBSERVER)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"

    def __str__(self) -> str:
        return self.email


class Employee(models.Model):
    """Сотрудник. Табельный номер входит в номер протокола (ЕИ-03-**05**-0147)."""

    user = models.OneToOneField(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="employee", verbose_name="учётная запись",
    )
    tab_number = models.CharField("табельный номер", max_length=8, unique=True)
    full_name = models.CharField("ФИО", max_length=200)
    short_name = models.CharField("ФИО сокращённо", max_length=80, blank=True)
    position = models.CharField("должность", max_length=120, blank=True)
    signature_image = models.ImageField("подпись", upload_to="signatures/", blank=True)
    stamp_image = models.ImageField("оттиск клейма", upload_to="stamps/", blank=True)
    is_active = models.BooleanField("работает", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.tab_number})"

    def can_sign(self, family, on_date: dt.date | None = None) -> bool:
        """Есть ли действующая аттестация на это семейство СИ."""
        on_date = on_date or timezone.localdate()
        return self.attestations.filter(
            families=family, valid_from__lte=on_date, valid_to__gte=on_date,
        ).exists()


class Attestation(models.Model):
    """Аттестация поверителя. Истекает — и человек теряет право подписи сам собой."""

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="attestations", verbose_name="сотрудник"
    )
    families = models.ManyToManyField(
        "catalog.MeasurementFamily", related_name="attestations", verbose_name="семейства СИ"
    )
    document = models.CharField("документ", max_length=120)
    valid_from = models.DateField("действует с")
    valid_to = models.DateField("действует по")
    scan = models.FileField("скан", upload_to="attestations/", blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "аттестация"
        verbose_name_plural = "аттестации"
        ordering = ["-valid_to"]

    def __str__(self) -> str:
        return f"{self.document} до {self.valid_to:%d.%m.%Y}"

    @property
    def is_valid(self) -> bool:
        today = timezone.localdate()
        return self.valid_from <= today <= self.valid_to


class Device(models.Model):
    """Полевое устройство. Потерянный телефон отзывается одной кнопкой."""

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="devices", verbose_name="сотрудник"
    )
    device_id = models.CharField("идентификатор", max_length=64, unique=True)
    label = models.CharField("название", max_length=120, blank=True)
    last_seen_at = models.DateTimeField("последняя синхронизация", null=True, blank=True)
    last_sync_cursor = models.BigIntegerField("курсор синхронизации", default=0)
    is_revoked = models.BooleanField("отозвано", default=False)
    created_at = models.DateTimeField("создано", auto_now_add=True)

    class Meta:
        verbose_name = "устройство"
        verbose_name_plural = "устройства"

    def __str__(self) -> str:
        return self.label or self.device_id
