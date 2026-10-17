"""Аттестация испытательного оборудования (ГОСТ Р 8.568-2017).

В отличие от поверки, протокол аттестации «нелинейный»: у каждого вида
оборудования своя программа и методика — свои характеристики, точки,
режимы и допуски. Поэтому структура протокола — это **план** (JSON,
``apps.equipment.calc.clean_plan``), который собирает ассистент, а проверяет
метролог. Измерения вносит только человек, расчёт и годность — код.
"""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


class AttestationProgram(models.Model):
    """Типовая программа и методика аттестации — заготовка плана для вида ИО.

    Ассистент берёт её за основу и уточняет по эксплуатационной документации
    конкретного оборудования (режимы, допуски).
    """

    title = models.CharField("название", max_length=250, unique=True)
    equipment_kind = models.CharField(
        "вид оборудования", max_length=200,
        help_text="Например «Холодильники фармацевтические», «Термостаты», «Шкафы сушильные»",
    )
    plan = models.JSONField("план-заготовка", default=dict, help_text="Формат — apps.equipment.calc.clean_plan")
    document = models.FileField("документ программы", upload_to="attestation_programs/", blank=True)
    note = models.TextField("примечание", blank=True)
    needs_review = models.BooleanField(
        "ждёт проверки метролога", default=True,
        help_text="Заготовка составлена по образцу протокола и ещё не сверена с программой аттестации",
    )
    is_active = models.BooleanField("используется", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "программа аттестации"
        verbose_name_plural = "программы аттестации"
        ordering = ["equipment_kind", "title"]

    def __str__(self) -> str:
        return self.title


class AttestationCounter(models.Model):
    """Последний выданный номер протокола аттестации за год (формат 1886/2026).

    Начальное значение года метролог выставляет в админке, если нумерация
    продолжает бумажную.
    """

    year = models.PositiveSmallIntegerField("год", unique=True)
    last = models.PositiveIntegerField("последний выданный номер", default=0)

    class Meta:
        verbose_name = "счётчик протоколов аттестации"
        verbose_name_plural = "счётчики протоколов аттестации"
        ordering = ["-year"]

    def __str__(self) -> str:
        return f"{self.year}: {self.last}"


class EquipmentAttestation(models.Model):
    class Kind(models.TextChoices):
        PRIMARY = "primary", "первичная"
        PERIODIC = "periodic", "периодическая"
        REPEAT = "repeat", "повторная"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        SIGNED = "signed", "Подписан"

    kind = models.CharField("вид аттестации", max_length=10, choices=Kind.choices, default=Kind.PERIODIC)
    status = models.CharField("статус", max_length=10, choices=Status.choices, default=Status.DRAFT)
    program = models.ForeignKey(
        AttestationProgram, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attestations", verbose_name="программа-заготовка",
    )

    # Оборудование и владелец — снимком: протокол показывает то, что было на дату.
    equipment = models.JSONField(
        "оборудование", default=dict,
        help_text="name, serial_number, manufacturer, year, owner, location, inventory_number",
    )
    commission = models.JSONField(
        "состав комиссии", default=list, help_text="[{role: chair|member, position, name}]",
    )
    instruments = models.JSONField(
        "средства измерений", default=list,
        help_text="[{name, serial_number, characteristics, verification, valid_until (YYYY-MM-DD)}]",
    )
    plan = models.JSONField("план аттестации", default=dict)
    plan_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="план проверил",
    )
    plan_approved_at = models.DateTimeField("план проверен", null=True, blank=True)
    texts = models.JSONField(
        "текстовые пункты", default=dict, blank=True,
        help_text="documentation, inspection, testing, notes, recommendations — пишет человек или ассистент",
    )
    plan_check = models.JSONField(
        "сверка плана с примером", default=dict, blank=True,
        help_text="Отчёт calc.check_example: результаты примера против посчитанных по плану. Числа примера "
                  "измерениями не являются и здесь не хранятся",
    )
    sources = models.JSONField(
        "откуда данные", default=list, blank=True,
        help_text="Таблица происхождения значений карточки и плана (для черновиков от ассистента)",
    )

    # Только от человека
    performed_on = models.DateField("дата проведения измерений", null=True, blank=True)
    conditions = models.JSONField(
        "условия проведения", default=dict, blank=True, help_text="temperature, humidity, pressure — измеренные",
    )
    measurements = models.JSONField("результаты измерений", default=dict, blank=True)
    measurements_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="измерения внёс",
    )
    measurements_at = models.DateTimeField("измерения внесены", null=True, blank=True)

    # Выпуск
    number = models.PositiveIntegerField("номер", null=True, blank=True)
    year = models.PositiveSmallIntegerField("год номера", null=True, blank=True)
    protocol_date = models.DateField("дата протокола", null=True, blank=True)
    results = models.JSONField("расчёт на момент подписи", default=dict, blank=True)
    suitable = models.BooleanField("соответствует", null=True, blank=True)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="подписал",
    )
    signed_at = models.DateTimeField("подписан", null=True, blank=True)
    snapshot = models.JSONField("содержимое протокола на момент подписи", default=dict, blank=True)
    sha256 = models.CharField("SHA-256 содержимого", max_length=64, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="кто завёл",
    )
    created_via_assistant = models.BooleanField("заведено ассистентом", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "аттестация ИО"
        verbose_name_plural = "аттестации ИО"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["year", "number"], name="equipment_attestation_unique_year_number",
                condition=models.Q(number__isnull=False),
            )
        ]

    def __str__(self) -> str:
        return f"{self.full_number or 'черновик'} — {self.equipment.get('name', '')} № {self.equipment.get('serial_number', '')}"

    @property
    def full_number(self) -> str:
        if self.number and self.year:
            return f"{self.number}/{self.year}"
        return ""

    @property
    def is_signed(self) -> bool:
        return self.status == self.Status.SIGNED

    @property
    def plan_approved(self) -> bool:
        return self.plan_approved_at is not None

    @staticmethod
    def digest(snapshot: dict) -> str:
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()
