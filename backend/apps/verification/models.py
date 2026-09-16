"""Поверки и протоколы — ядро системы.

Журнал учёта поверочных работ здесь не сущность. Сущность — Verification;
журнал это отсортированный взгляд на неё, который умеет выгружаться в Excel.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
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
    postal_code = models.CharField("индекс", max_length=6, blank=True)
    fias_id = models.CharField(
        "ФИАС ID", max_length=64, blank=True,
        help_text="Заполняется, когда адрес выбран из подсказки (DaData), а не введён вручную",
    )
    district = models.ForeignKey(
        "catalog.District", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sites", verbose_name="район",
    )
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
    work_order = models.ForeignKey(
        "WorkOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="verifications", verbose_name="наряд",
        help_text="Пусто, если поверка заведена не из наряда (например, импорт истории)",
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
    needs_review = models.BooleanField(
        "есть строки на сверку", default=False, db_index=True,
        help_text="Распознано неуверенно или объём по эталону разошёлся с расчётным. "
                  "Признак вынесен из JSON отдельным полем — по нему фильтруется журнал",
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
    """Область нумерации: связка «серия + поверитель + год».

    Номер протокола: ЕИ-{серия}-{табельный}-{порядковый}{подномер}

    Счётчик привязан к СЕРИИ, а не к виду СИ: в журнале за 2021–2026 годы
    счётчики воды, весы и гири идут одной сквозной последовательностью 03.
    Обычный sequence не подходит — счётчик свой на каждого поверителя и год.
    """

    series = models.ForeignKey(
        "catalog.NumberingSeries", on_delete=models.PROTECT, related_name="numbering_scopes",
        verbose_name="серия",
    )
    employee = models.ForeignKey(
        "core.Employee", on_delete=models.PROTECT, related_name="numbering_scopes",
        verbose_name="поверитель",
    )
    year = models.PositiveSmallIntegerField(
        "год", null=True, blank=True,
        help_text="Счётчик сбрасывается 1 января, поэтому область нумерации годовая. "
                  "NULL только у серий со сквозной нумерацией",
    )

    class Meta:
        verbose_name = "область нумерации"
        verbose_name_plural = "области нумерации"
        constraints = [
            models.UniqueConstraint(
                fields=["series", "employee", "year"], name="numberingscope_unique",
                nulls_distinct=False,
            )
        ]

    def __str__(self) -> str:
        tail = f" / {self.year}" if self.year else ""
        return f"ЕИ-{self.series.code}-{self.employee.tab_number}{tail}"


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
        "подномер", max_length=4, blank=True, default="",
        help_text="Для вставки задним числом в уже запечатанный участок: 00767/1. "
                  "Формат взят из журнала — так уже делали руками",
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
        series = self.scope.series
        return (
            f"ЕИ-{series.code}-{self.scope.employee.tab_number}-"
            f"{self.seq:0{series.digits}d}{self.suffix}"
        )

    @property
    def is_sealed(self) -> bool:
        return self.status in ProtocolStatus.sealed()

# ---------------------------------------------------------------------------
# Сканы бумажных бланков (второй срез офлайна — QR + распознавание)
# ---------------------------------------------------------------------------
class ScanUpload(models.Model):
    """Фото бумажного бланка — второй сценарий офлайна (см. claude/scans.md).

    Бланк печатается системой (``render.render_blank``) с QR наряда; какой
    именно счётчик попадёт на бланк, заранее не известно (см. WorkOrder —
    заявка может включать несколько счётчиков), поэтому QR кодирует только
    наряд, не СИ. Один наряд может получить несколько сканов — по одному на
    каждый счётчик, печатаются и заполняются они отдельно.
    """

    work_order = models.ForeignKey(
        "WorkOrder", on_delete=models.CASCADE, related_name="scans", verbose_name="наряд",
    )
    verification = models.ForeignKey(
        Verification, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scans", verbose_name="поверка",
        help_text="Заполняется после распознавания — какую поверку завела эта фотография",
    )
    uploaded_by = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scan_uploads", verbose_name="загрузил",
    )
    image = models.ImageField("фото бланка", upload_to="scans/%Y/%m/")
    recognized = models.JSONField(
        "распознано VLM", default=dict, blank=True,
        help_text="Сырой ответ распознавания — для отладки и метрики доли строк, "
                  "исправленных человеком на экране сверки",
    )
    error = models.TextField(
        "ошибка обработки", blank=True,
        help_text="QR не найден, реперные метки не выровнялись, распознавание "
                  "недоступно и т. п. — поверитель в этом случае вводит вручную",
    )
    created_at = models.DateTimeField("создано", auto_now_add=True)

    class Meta:
        verbose_name = "скан бланка"
        verbose_name_plural = "сканы бланков"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Скан по наряду №{self.work_order_id} от {self.created_at:%d.%m.%Y %H:%M}"


# ---------------------------------------------------------------------------
# Фото прибора при поверке
# ---------------------------------------------------------------------------
class VerificationPhoto(models.Model):
    """Рабочее фото конкретного СИ, привязанное к поверке.

    Не путать со ``ScanUpload`` — тот один на бланк целиком и существует ради
    OCR/VLM-распознавания показаний (второй срез офлайна, см.
    ``claude/scans.md``). Здесь же — просто фото прибора (табличка,
    повреждение, место установки), без какого-либо распознавания, любое их
    число на одну поверку.

    Как и ``ScanUpload``, привязка к поверке, а не напрямую к экземпляру СИ
    (``Instrument``): один и тот же прибор поверяется многократно, и фото с
    конкретного выезда должно остаться при конкретном акте, а не расплыться
    по всей истории прибора.

    Загрузка — только при наличии связи (см. FieldWork.tsx): в отличие от
    самих измерений (``offline/sync.ts``), фото в офлайн-очередь пока не
    ставятся — большие файлы в IndexedDB и без того ограниченной офлайн-базы
    того не стоили на этом этапе.
    """

    verification = models.ForeignKey(
        Verification, on_delete=models.CASCADE, related_name="photos", verbose_name="поверка",
    )
    uploaded_by = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="verification_photos", verbose_name="загрузил",
    )
    image = models.ImageField("фото", upload_to="verification_photos/%Y/%m/")
    caption = models.CharField("подпись", max_length=200, blank=True)
    created_at = models.DateTimeField("создано", auto_now_add=True)

    class Meta:
        verbose_name = "фото СИ"
        verbose_name_plural = "фото СИ"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Фото поверки №{self.verification_id} от {self.created_at:%d.%m.%Y %H:%M}"


# ---------------------------------------------------------------------------
# Наряды
# ---------------------------------------------------------------------------
class WorkOrderStatus(models.TextChoices):
    PLANNED = "planned", "Запланирован"
    IN_PROGRESS = "in_progress", "В работе"
    DONE = "done", "Закрыт"
    CANCELLED = "cancelled", "Отменён"


class WorkOrder(models.Model):
    """Наряд — задание поверителю на выезд или приём.

    Наряд не хранит измерения сам — каждая поверка внутри него своя
    (Verification.work_order), а закрывается наряд, когда все его поверки
    приняты нормоконтролем (см. apps.verification.signals).
    """

    request = models.ForeignKey(
        "hub.Request", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="work_orders", verbose_name="заявка",
        help_text="Пусто, если наряд заведён напрямую, не из заявки с сайта",
    )
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name="work_orders", verbose_name="клиент"
    )
    site = models.ForeignKey(
        Site, on_delete=models.PROTECT, related_name="work_orders", verbose_name="объект"
    )
    assigned_employee = models.ForeignKey(
        "core.Employee", on_delete=models.PROTECT, related_name="work_orders",
        verbose_name="исполнитель",
    )
    status = models.CharField(
        "статус", max_length=12, choices=WorkOrderStatus.choices, default=WorkOrderStatus.PLANNED
    )
    scheduled_date = models.DateField("плановая дата", null=True, blank=True)
    scheduled_time = models.TimeField(
        "плановое время", null=True, blank=True,
        help_text="Переносится из Request.desired_time при подтверждении заявки, "
                  "если среди приборов был хоть один, требующий выбора времени",
    )
    note = models.TextField("примечание", blank=True)

    created_at = models.DateTimeField("создан", auto_now_add=True)
    closed_at = models.DateTimeField("закрыт", null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "наряд"
        verbose_name_plural = "наряды"
        ordering = ["-scheduled_date", "-created_at"]
        indexes = [models.Index(fields=["status", "scheduled_date"])]

    def __str__(self) -> str:
        return f"Наряд №{self.pk} — {self.site}"

    @property
    def is_closed(self) -> bool:
        return self.status in (WorkOrderStatus.DONE, WorkOrderStatus.CANCELLED)

    def refresh_status(self) -> None:
        """Пересчитать статус по состоянию поверок. Вызывается сигналом при их сохранении.

        Отменённый наряд руками — состояние ручное, автоматика его не трогает.
        """
        if self.status == WorkOrderStatus.CANCELLED:
            return
        verifications = list(self.verifications.all())
        if not verifications:
            return
        all_accepted = all(v.status == VerificationStatus.ACCEPTED for v in verifications)
        if all_accepted and self.status != WorkOrderStatus.DONE:
            self.status = WorkOrderStatus.DONE
            self.closed_at = timezone.now()
            self.save(update_fields=["status", "closed_at"])
            self.sync_request_status()
        elif not all_accepted and self.status in (WorkOrderStatus.PLANNED, WorkOrderStatus.DONE):
            # DONE -> IN_PROGRESS: нормоконтроль вернул один из протоколов на
            # доработку уже после закрытия наряда — наряд открывается снова.
            self.status = WorkOrderStatus.IN_PROGRESS
            self.closed_at = None
            self.save(update_fields=["status", "closed_at"])
            self.sync_request_status()

    def sync_request_status(self) -> None:
        """Отразить свой статус на заявке, из которой заведён (если заведён).

        До этого метода наряд можно было отменить (или вернуть из отмены)
        вручную (``WorkOrderStatusView``), а заявка так и оставалась
        «Подтверждена» — работник не мог увидеть по заявке, что с ней
        случилось дальше. Теперь наряд — источник истины для судьбы заявки:

        * DONE      -> заявка DONE
        * CANCELLED -> заявка CANCELLED
        * иначе     -> заявка CONFIRMED (в т. ч. возврат из DONE/CANCELLED,
          если наряд открыли заново)

        Заявку, которую диспетчер уже отклонил или пометил спамом
        (REJECTED/SPAM), а также ещё не подтверждённую (NEW/ROUTED), наряд не
        трогает — таких сочетаний в обычном пути не бывает (наряд заводится
        только при подтверждении заявки), но это защита от неожиданностей,
        а не часть сценария.

        Вызывается и из ``refresh_status()`` (автопересчёт по поверкам), и из
        ``WorkOrderStatusView`` (ручная отмена/возврат — при CANCELLED
        ``refresh_status()`` выходит на первой строке и сюда не доходит).
        """
        if self.request_id is None:
            return
        from apps.hub.models import RequestStatus  # локально — без цикла на уровне модулей

        request = self.request
        if request.status not in (RequestStatus.CONFIRMED, RequestStatus.DONE, RequestStatus.CANCELLED):
            return
        mapping = {
            WorkOrderStatus.DONE: RequestStatus.DONE,
            WorkOrderStatus.CANCELLED: RequestStatus.CANCELLED,
        }
        new_status = mapping.get(self.status, RequestStatus.CONFIRMED)
        if request.status != new_status:
            request.status = new_status
            request.save(update_fields=["status"])

