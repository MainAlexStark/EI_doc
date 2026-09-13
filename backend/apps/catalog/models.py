"""Нормативно-справочная информация.

Всё, что раньше лежало в config.yaml на машине поверителя и в именах файлов
шаблонов, живёт здесь и правится метрологом через админку.
"""

from __future__ import annotations

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class MeasurementFamily(models.Model):
    """Семейство СИ: счётчики воды, манометры и т. д.

    Определяет номер типа в протоколе (03 для воды), схему измерений,
    калькулятор и правила нумерации.
    """

    code = models.SlugField("код", max_length=40, unique=True)
    name = models.CharField("наименование", max_length=160)
    type_code = models.CharField(
        "номер типа в протоколе", max_length=2,
        validators=[RegexValidator(r"^\d{2}$", "Две цифры, например 03")],
        help_text="Вторая группа в номере протокола: ЕИ-<ЭТО>-05-0147",
    )
    measurement_schema = models.JSONField(
        "JSON Schema измерений", default=dict, blank=True,
        help_text="Проверяет структуру Verification.measurements для этого семейства",
    )
    calculator_key = models.CharField(
        "ключ калькулятора", max_length=60, blank=True,
        help_text="Имя модуля расчёта, например water_meter",
    )
    numbering_resets_yearly = models.BooleanField(
        "нумерация сбрасывается ежегодно", default=False,
        help_text="ВОПРОС К МЕТРОЛОГУ: сейчас формат номера года не содержит, "
                  "значит нумерация сквозная. Переключить, если это не так.",
    )
    uses_number_blocks = models.BooleanField(
        "выдавать блоки номеров на выезд", default=False,
        help_text="Нужно, если поверитель обязан назвать номер протокола на объекте",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "семейство СИ"
        verbose_name_plural = "семейства СИ"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.type_code})"


class SiType(models.Model):
    """Тип СИ из Госреестра. Подтягивается из открытого реестра ФИФ и кэшируется."""

    family = models.ForeignKey(
        MeasurementFamily, on_delete=models.PROTECT, related_name="si_types",
        verbose_name="семейство",
    )
    registry_number = models.CharField("регистрационный номер", max_length=40)
    name = models.CharField("наименование", max_length=200)
    manufacturer = models.CharField("изготовитель", max_length=200, blank=True)
    verification_interval_months = models.PositiveSmallIntegerField(
        "межповерочный интервал, мес.", default=72
    )
    fif_url = models.URLField("ссылка на ФИФ", blank=True)
    fif_raw = models.JSONField("ответ ФИФ как есть", default=dict, blank=True)
    is_active = models.BooleanField("используется", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "тип СИ"
        verbose_name_plural = "типы СИ"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["registry_number", "name"], name="sitype_unique_registry_name"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.registry_number})"


class Standard(models.Model):
    """Эталон. Система сама предупредит, когда у него истекает срок поверки."""

    fif_number = models.CharField("номер в ФИФ", max_length=60, unique=True)
    name = models.CharField("наименование", max_length=200)
    serial_number = models.CharField("заводской номер", max_length=60, blank=True)
    verified_at = models.DateField("поверен")
    valid_until = models.DateField("годен до")
    location = models.CharField("где находится", max_length=160, blank=True)
    is_active = models.BooleanField("в работе", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "эталон"
        verbose_name_plural = "эталоны"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} № {self.fif_number}"

    @property
    def is_valid(self) -> bool:
        return self.is_active and self.valid_until >= timezone.localdate()


class VerificationMethod(models.Model):
    designation = models.CharField("обозначение", max_length=120, unique=True)
    name = models.CharField("наименование", max_length=250, blank=True)
    families = models.ManyToManyField(
        MeasurementFamily, related_name="methods", verbose_name="семейства СИ"
    )
    document = models.FileField("документ", upload_to="methods/", blank=True)

    class Meta:
        verbose_name = "методика поверки"
        verbose_name_plural = "методики поверки"
        ordering = ["designation"]

    def __str__(self) -> str:
        return self.designation


class ProtocolTemplate(models.Model):
    """Исходник Typst-шаблона.

    Протокол хранит версию, по которой был выпущен: перевыпуск документа
    двухлетней давности должен дать тот же документ, который у клиента на руках.
    """

    family = models.ForeignKey(
        MeasurementFamily, on_delete=models.PROTECT, related_name="templates",
        verbose_name="семейство",
    )
    version = models.PositiveIntegerField("версия")
    source = models.TextField("исходник Typst")
    comment = models.CharField("что изменилось", max_length=250, blank=True)
    valid_from = models.DateField("применяется с", default=timezone.localdate)
    is_active = models.BooleanField("активна", default=True)
    created_at = models.DateTimeField("создана", auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "шаблон протокола"
        verbose_name_plural = "шаблоны протоколов"
        ordering = ["family", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["family", "version"], name="protocoltemplate_unique_family_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.family.code} v{self.version}"
