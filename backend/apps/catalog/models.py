"""Нормативно-справочная информация.

Всё, что раньше лежало в config.yaml на машине поверителя и в именах файлов
шаблонов, живёт здесь и правится метрологом через админку.
"""

from __future__ import annotations

from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class NumberingSeries(models.Model):
    """Серия нумерации протоколов — вторая группа в номере: ЕИ-**03**-02-00767.

    Важно: серия НЕ совпадает с видом СИ. В журнале за 2021–2026 годы счётчики
    воды, весы, гири и дозаторы идут в одной сквозной серии 03, а манометры
    частью в 03, частью в отдельной серии 01. Поэтому счётчик протоколов общий
    на серию, а не на семейство СИ.
    """

    code = models.CharField(
        "код", max_length=2, unique=True,
        validators=[RegexValidator(r"^\d{2}$", "Две цифры, например 03")],
    )
    name = models.CharField("название", max_length=120)
    digits = models.PositiveSmallIntegerField(
        "разрядов в порядковом номере", default=5, help_text="ЕИ-03-02-00767 — пять"
    )
    resets_yearly = models.BooleanField("сбрасывается в начале года", default=True)
    is_default = models.BooleanField(
        "по умолчанию", default=False,
        help_text="Используется семействами СИ, у которых серия не указана явно",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "серия нумерации"
        verbose_name_plural = "серии нумерации"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"ЕИ-{self.code} — {self.name}"

    @classmethod
    def default(cls) -> "NumberingSeries | None":
        return cls.objects.filter(is_default=True).first()


class MeasurementFamily(models.Model):
    """Семейство СИ: счётчики воды, манометры и т. д.

    Определяет номер типа в протоколе (03 для воды), схему измерений,
    калькулятор и правила нумерации.
    """

    code = models.SlugField("код", max_length=40, unique=True)
    name = models.CharField("наименование", max_length=160)
    numbering_series = models.ForeignKey(
        NumberingSeries, on_delete=models.PROTECT, related_name="families",
        null=True, blank=True, verbose_name="серия нумерации",
        help_text="Пусто — берётся серия по умолчанию. Несколько семейств СИ "
                  "в одной серии делят общий счётчик протоколов",
    )
    measurement_schema = models.JSONField(
        "JSON Schema измерений", default=dict, blank=True,
        help_text="Проверяет структуру Verification.measurements для этого семейства",
    )
    calculator_key = models.CharField(
        "ключ калькулятора", max_length=60, blank=True,
        help_text="Имя модуля расчёта, например water_meter",
    )
    condition_ranges = models.JSONField(
        "диапазоны условий поверки", default=dict, blank=True,
        help_text="Переопределяет общий профиль условий для этого семейства, "
                  "см. apps.catalog.conditions",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "семейство СИ"
        verbose_name_plural = "семейства СИ"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def series(self) -> NumberingSeries | None:
        return self.numbering_series or NumberingSeries.default()


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
    limits = models.JSONField(
        "метрологические характеристики", default=dict, blank=True,
        help_text="Для счётчиков воды: q_min, q_transition_a, q_transition_b, q_nominal, "
                  "q_max, error_below_transition, error_above_transition. "
                  "Читается apps.verification.calculators.water_meter.MeterLimits",
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


class ConditionProfile(models.Model):
    """Диапазоны условий поверки — то, что раньше лежало в config.yaml.

    Формат `ranges` — как в apps.catalog.conditions.DEFAULT_RANGES:
    {"temperature": {"min": 18.0, "max": 25.0, "decimals": 1}, …}
    Указывать можно только те ключи, которые отличаются от значений по умолчанию.
    """

    name = models.CharField("название", max_length=120, default="Основной")
    ranges = models.JSONField("диапазоны", default=dict, blank=True)
    is_active = models.BooleanField("активен", default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "профиль условий поверки"
        verbose_name_plural = "профили условий поверки"

    def __str__(self) -> str:
        return self.name


class AmbientRecord(models.Model):
    """Журнал погоды: одна запись на дату.

    Все протоколы за один день показывают одни и те же условия в помещении —
    это поведение старого приложения, и оно сохранено намеренно.
    """

    date = models.DateField("дата", unique=True)
    temperature = models.DecimalField("температура, °C", max_digits=5, decimal_places=1)
    pressure = models.DecimalField("давление, кПа", max_digits=6, decimal_places=1)
    humidity = models.DecimalField("влажность, %", max_digits=4, decimal_places=1)
    is_generated = models.BooleanField(
        "сгенерировано", default=True,
        help_text="Снято, если значения занесены как реально измеренные",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "запись журнала погоды"
        verbose_name_plural = "журнал погоды"
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.date:%d.%m.%Y}: {self.temperature} °C, {self.pressure} кПа, {self.humidity} %"


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
