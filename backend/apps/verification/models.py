"""Поверки и протоколы — ядро системы.

Журнал учёта поверочных работ здесь не сущность. Сущность — Verification;
журнал это отсортированный взгляд на неё, который умеет выгружаться в Excel.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords


# ---------------------------------------------------------------------------
# Клиенты и объекты
# ---------------------------------------------------------------------------
class ClientKind(models.TextChoices):
    PERSON = "person", "Частное лицо"
    ORG = "org", "Юридическое лицо"


class Client(models.Model):
    kind = models.CharField("тип", max_length=10, choices=ClientKind.choices, default=ClientKind.PERSON)
    name = models.CharField("наименование / ФИО", max_length=250)
    inn = models.CharField("ИНН", max_length=12, blank=True, db_index=True)
    phone = models.CharField("телефон", max_length=32, blank=True)
    email = models.EmailField("email", blank=True)
    note = models.TextField("примечание", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "клиент"
        verbose_name_plural = "клиенты"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Site(models.Model):
    """Объект поверки.

    Координаты — обычные широта/долгота. PostGIS подключается на этапе 4
    вместе с картой и маршрутами, чтобы не тащить GDAL в образ раньше времени.
    """

    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="sites", null=True, blank=True,
        verbose_name="клиент",
    )
    address = models.CharField("адрес", max_length=350)
    latitude = models.DecimalField("широта", max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField("долгота", max_digits=9, decimal_places=6, null=True, blank=True)
    is_restricted = models.BooleanField(
        "режимный объект", default=False,
        help_text="Телефон пронести нельзя — работа по бумажному бланку",
    )
    access_note = models.TextField("порядок допуска", blank=True)
    contact = models.CharField("контактное лицо", max_length=160, blank=True)

    class Meta:
        verbose_name = "объект"
        verbose_name_plural = "объекты"
        ordering = ["address"]

    def __str__(self) -> str:
        return self.address


class Instrument(models.Model):
    """Экземпляр СИ. Живёт между поверками: вторая поверка видит первую."""

    si_type = models.ForeignKey(
        "catalog.SiType", on_delete=models.PROTECT, related_name="instruments",
        verbose_name="тип СИ",
    )
    serial_number = models.CharField("заводской номер", max_length=60)
    manufacture_year = models.PositiveSmallIntegerField("год выпуска", null=True, blank=True)
    owner = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="instruments", null=True, blank=True,
        verbose_name="владелец",
    )
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name="instruments", null=True, blank=True,
        verbose_name="место установки",
    )
    attributes = models.JSONField("доп. характеристики", default=dict, blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "экземпляр СИ"
        verbose_name_plural = "экземпляры СИ"
        constraints = [
            models.UniqueConstraint(
                fields=["si_type", "serial_number"], name="instrument_unique_type_serial"
            )
        ]

    def __str__(self) -> str:
        return f"{self.si_type.name} № {self.serial_number}"


# ---------------------------------------------------------------------------
# Поверка
# ---------------------------------------------------------------------------
class ConditionSource(models.TextChoices):
    """Откуда взялось значение условия поверки.

    ВНУТРЕННЕЕ ПОЛЕ. В протокол не выводится и клиенту не показывается —
    решение от 13.09.2026. Само поведение старого приложения сохранено:
    недостающие условия генерируются в нормативном диапазоне и запоминаются
    в журнале погоды (см. apps.catalog.conditions). Разница только в том,
    что теперь система помнит, какое значение измерено, а какое подставлено.
    """

    MEASURED = "measured", "Измерено"
    ARCHIVE = "archive", "Из журнала погоды"
    GENERATED = "generated", "Сгенерировано в нормативном диапазоне"


class VerificationStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    READY = "ready", "Готова к нормоконтролю"
    ACCEPTED = "accepted", "Принята"
    REJECTED = "rejected", "Возвращена на доработку"


class Verification(models.Model):
    """Акт поверки. verified_at определяет место протокола в нумерации."""

    # Идемпотентность офлайн-синхронизации: повторная отправка той же операции
    # с того же устройства ничего не дублирует.
    client_id = models.UUIDField("идентификатор с устройства", default=uuid.uuid4, unique=True)
    device = models.ForeignKey(
        "core.Device", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="verifications", verbose_name="устройство",
    )

    instrument = models.ForeignKey(
        Instrument, on_delete=models.PROTECT, related_name="verifications", verbose_name="СИ"
    )
    verifier = models.ForeignKey(
        "core.Employee", on_delete=models.PROTECT, related_name="verifications",
        verbose_name="поверитель",
    )
    method = models.ForeignKey(
        "catalog.VerificationMethod", on_delete=models.PROTECT, null=True, blank=True,
        related_name="verifications", verbose_name="методика",
    )
    standards = models.ManyToManyField(
        "catalog.Standard", related_name="verifications", verbose_name="применённые эталоны"
    )

    verified_at = models.DateTimeField(
        "дата и время поверки", db_index=True,
        help_text="Определяет порядковый номер протокола — номера идут строго по времени поверки",
    )
    next_verification_date = models.DateField("следующая поверка", null=True, blank=True)

    suitable = models.BooleanField("признан годным", default=True)
    unsuitability_reason = models.TextField("причины непригодности", blank=True)

    # Условия поверки. Каждое значение — вместе с источником (внутреннее поле).
    temperature = models.DecimalField("температура, °C", max_digits=5, decimal_places=1, null=True, blank=True)
    temperature_source = models.CharField(max_length=10, choices=ConditionSource.choices, default=ConditionSource.MEASURED)
    pressure = models.DecimalField("давление, кПа", max_digits=6, decimal_places=1, null=True, blank=True)
    pressure_source = models.CharField(max_length=10, choices=ConditionSource.choices, default=ConditionSource.MEASURED)
    humidity = models.DecimalField("влажность, %", max_digits=4, decimal_places=1, null=True, blank=True)
    humidity_source = models.CharField(max_length=10, choices=ConditionSource.choices, default=ConditionSource.MEASURED)

    measurements = models.JSONField(
        "измерения", default=dict, blank=True,
        help_text="Структура задаётся MeasurementFamily.measurement_schema",
    )
    results = models.JSONField(
        "результаты расчёта", default=dict, blank=True,
        help_text="Заполняется калькулятором семейства, не руками",
    )

    status = models.CharField(
        "статус", max_length=12, choices=VerificationStatus.choices, default=VerificationStatus.DRAFT
    )
    review_note = models.TextField("замечание нормоконтроля", blank=True)

    created_at = models.DateTimeField("создана", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("изменена", auto_now=True)
    synced_at = models.DateTimeField("синхронизирована", null=True, blank=True)

    history = HistoricalRecords()

    # Поля, которые никогда не попадают в протокол и во внешние выгрузки.
    INTERNAL_ONLY_FIELDS = ("temperature_source", "pressure_source", "humidity_source", "review_note")

    class Meta:
        verbose_name = "поверка"
        verbose_name_plural = "поверки"
        ordering = ["-verified_at"]
        indexes = [models.Index(fields=["verifier", "verified_at"])]

    def __str__(self) -> str:
        return f"{self.instrument} от {self.verified_at:%d.%m.%Y}"

    @property
    def family(self):
        return self.instrument.si_type.family

    def clean(self) -> None:
        if not self.suitable and not self.unsuitability_reason:
            raise ValidationError(
                {"unsuitability_reason": "Причины непригодности обязательны, если СИ признан негодным."}
            )

    @property
    def has_substituted_conditions(self) -> bool:
        """Хотя бы одно условие не измерено, а подставлено. Только для внутренних отчётов."""
        return any(
            source != ConditionSource.MEASURED
            for source in (self.temperature_source, self.pressure_source, self.humidity_source)
        )


# ---------------------------------------------------------------------------
# Нумерация и протокол
# ---------------------------------------------------------------------------
class NumberingScope(models.Model):
    """Область нумерации: связка «семейство СИ + поверитель [+ год]».

    Номер протокола: ЕИ-{type_code}-{tab_number}-{seq:04d}{suffix}
    Счётчик свой на каждую такую связку — обычный sequence не подходит.
    """

    family = models.ForeignKey(
        "catalog.MeasurementFamily", on_delete=models.PROTECT, related_name="numbering_scopes",
        verbose_name="семейство",
    )
    employee = models.ForeignKey(
        "core.Employee", on_delete=models.PROTECT, related_name="numbering_scopes",
        verbose_name="поверитель",
    )
    year = models.PositiveSmallIntegerField(
        "год", null=True, blank=True,
        help_text="Счётчик сбрасывается в начале года, поэтому область нумерации — годовая. "
                  "NULL только у семейств со сквозной нумерацией",
    )

    class Meta:
        verbose_name = "область нумерации"
        verbose_name_plural = "области нумерации"
        constraints = [
            models.UniqueConstraint(
                fields=["family", "employee", "year"], name="numberingscope_unique",
                nulls_distinct=False,
            )
        ]

    def __str__(self) -> str:
        tail = f" / {self.year}" if self.year else ""
        return f"ЕИ-{self.family.type_code}-{self.employee.tab_number}{tail}"


class ProtocolStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"          # номера ещё нет
    NUMBERED = "numbered", "Номер присвоен"  # можно пересчитать
    SIGNED = "signed", "Подписан"        # запечатан, номер неизменяем
    PUBLISHED = "published", "Опубликован в ФИФ"
    VOID = "void", "Аннулирован"         # номер занят навсегда

    @classmethod
    def sealed(cls) -> tuple[str, ...]:
        """Статусы, после которых номер трогать нельзя."""
        return (cls.SIGNED, cls.PUBLISHED, cls.VOID)


class Protocol(models.Model):
    """Документ по поверке. Номер присваивается сервисом нумерации, не руками."""

    verification = models.OneToOneField(
        Verification, on_delete=models.PROTECT, related_name="protocol", verbose_name="поверка"
    )
    scope = models.ForeignKey(
        NumberingScope, on_delete=models.PROTECT, related_name="protocols",
        verbose_name="область нумерации",
    )

    seq = models.PositiveIntegerField(
        "порядковый номер", null=True, blank=True,
        help_text="NULL у черновика. Пересчитывается, пока протокол не подписан",
    )
    suffix = models.CharField(
        "литера", max_length=2, blank=True, default="",
        help_text="Для вставки задним числом в уже запечатанный участок: 0147А",
    )
    out_of_sequence_reason = models.CharField(
        "причина вставки вне очереди", max_length=250, blank=True
    )
    field_number = models.CharField(
        "номер, записанный на свидетельстве в поле", max_length=40, blank=True, db_index=True,
        help_text="Когда у поверителя на объекте нет доступа к базе, на свидетельстве "
                  "пишется номер «на месте». Здесь он сохраняется и связывается с настоящим — "
                  "иначе бумагу на руках у клиента потом не найти",
    )

    status = models.CharField(
        "статус", max_length=12, choices=ProtocolStatus.choices, default=ProtocolStatus.DRAFT
    )
    template = models.ForeignKey(
        "catalog.ProtocolTemplate", on_delete=models.PROTECT, null=True, blank=True,
        related_name="protocols", verbose_name="версия шаблона",
    )

    pdf = models.FileField("PDF", upload_to="protocols/%Y/%m/", blank=True)
    sha256 = models.CharField("SHA-256 файла", max_length=64, blank=True)

    numbered_at = models.DateTimeField("номер присвоен", null=True, blank=True)
    signed_at = models.DateTimeField("подписан", null=True, blank=True)
    signed_by = models.ForeignKey(
        "core.Employee", on_delete=models.PROTECT, null=True, blank=True,
        related_name="signed_protocols", verbose_name="подписал",
    )
    published_at = models.DateTimeField("опубликован в ФИФ", null=True, blank=True)
    fif_record_number = models.CharField("номер записи в ФИФ", max_length=60, blank=True)

    void_reason = models.CharField("причина аннулирования", max_length=250, blank=True)
    supersedes = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
        verbose_name="заменяет протокол",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "протокол"
        verbose_name_plural = "протоколы"
        ordering = ["scope", "seq", "suffix"]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "seq", "suffix"],
                condition=models.Q(seq__isnull=False),
                name="protocol_unique_number",
            )
        ]
        indexes = [models.Index(fields=["scope", "status"])]

    def __str__(self) -> str:
        return self.full_number or f"черновик #{self.pk}"

    @property
    def full_number(self) -> str:
        if self.seq is None:
            return ""
        scope = self.scope
        digits = scope.family.number_digits
        return (
            f"ЕИ-{scope.family.type_code}-{scope.employee.tab_number}-"
            f"{self.seq:0{digits}d}{self.suffix}"
        )

    @property
    def is_sealed(self) -> bool:
        return self.status in ProtocolStatus.sealed()
