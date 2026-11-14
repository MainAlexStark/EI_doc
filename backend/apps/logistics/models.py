"""Командировки: обычные и по закупкам (claude/trips.md).

Командировка — выезд на несколько дней с перечнем адресов. Адрес командировки
(``TripStop``) оборачивает обычный наряд (``verification.WorkOrder``), поэтому
поверка счётчиков на адресе идёт тем же экраном и той же офлайн-очередью, что
и городской выезд, — второго способа завести поверку здесь нет.

Видов два:

* **обычная** — район, даты, адреса из плана и те, что добавились на месте;
* **по закупке** — главное здесь контракт (``Procurement``): номер закупки,
  заказчик, документы, пропуска. Контракт исполняется долго и в несколько
  заходов, поэтому командировок у одной закупки сколько угодно, а объём
  (план/факт по приборам) считается по контракту целиком.
"""

from __future__ import annotations

import datetime as dt

from django.db import models
from simple_history.models import HistoricalRecords


# ---------------------------------------------------------------------------
# Закупка / контракт
# ---------------------------------------------------------------------------
class ProcurementLaw(models.TextChoices):
    FZ44 = "44", "44-ФЗ"
    FZ223 = "223", "223-ФЗ"
    DIRECT = "direct", "Прямой договор"
    OTHER = "other", "Другое"


class ProcurementStatus(models.TextChoices):
    PREPARING = "preparing", "Подготовка"
    ACTIVE = "active", "Исполняется"
    DONE = "done", "Исполнен"
    CANCELLED = "cancelled", "Расторгнут"


class Procurement(models.Model):
    """Закупка и заключённый по ней контракт — основание командировок «по закупке»."""

    number = models.CharField(
        "номер закупки", max_length=40, blank=True, db_index=True,
        help_text="Номер извещения в ЕИС (zakupki.gov.ru) или на площадке",
    )
    law = models.CharField("основание", max_length=8, choices=ProcurementLaw.choices, default=ProcurementLaw.FZ44)
    title = models.CharField("предмет", max_length=300)
    customer = models.ForeignKey(
        "verification.Client", on_delete=models.PROTECT, related_name="procurements",
        verbose_name="заказчик",
    )
    contract_number = models.CharField("номер контракта", max_length=80, blank=True)
    contract_date = models.DateField("дата контракта", null=True, blank=True)
    date_start = models.DateField("начало работ", null=True, blank=True)
    deadline = models.DateField("срок исполнения", null=True, blank=True)
    amount = models.DecimalField("цена контракта, ₽", max_digits=12, decimal_places=2, null=True, blank=True)
    eis_url = models.URLField("ссылка на закупку", max_length=400, blank=True)
    status = models.CharField(
        "состояние", max_length=10, choices=ProcurementStatus.choices, default=ProcurementStatus.ACTIVE
    )
    responsible = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="procurements", verbose_name="ответственный",
    )
    contact = models.CharField(
        "контакт у заказчика", max_length=250, blank=True,
        help_text="Кто встречает и подписывает акты: должность, ФИО, телефон",
    )
    note = models.TextField("примечание", blank=True)
    created_at = models.DateTimeField("заведена", auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "закупка"
        verbose_name_plural = "закупки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.label} — {self.title}"

    @property
    def label(self) -> str:
        """Как закупку называют вслух: номер закупки, иначе номер контракта."""
        if self.number:
            return f"№ {self.number}"
        if self.contract_number:
            return f"Контракт № {self.contract_number}"
        return f"Закупка {self.pk}"

    @property
    def search_url(self) -> str:
        """Куда вести по кнопке «Открыть в ЕИС»: своя ссылка, иначе поиск по номеру."""
        if self.eis_url:
            return self.eis_url
        if self.number and self.law in (ProcurementLaw.FZ44, ProcurementLaw.FZ223):
            return f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={self.number}"
        return ""


class ProcurementItem(models.Model):
    """Строка объёма по контракту: что и сколько надо поверить. План для «план/факт»."""

    procurement = models.ForeignKey(
        Procurement, on_delete=models.CASCADE, related_name="items", verbose_name="закупка"
    )
    title = models.CharField("что", max_length=250)
    quantity = models.PositiveIntegerField("количество", default=1)
    position = models.PositiveSmallIntegerField("порядок", default=0)

    class Meta:
        verbose_name = "строка объёма"
        verbose_name_plural = "объём по контракту"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"{self.title} — {self.quantity}"


class DocumentKind(models.TextChoices):
    NOTICE = "notice", "Извещение / ТЗ"
    CONTRACT = "contract", "Контракт"
    PASS_LIST = "pass_list", "Заявка на пропуска"
    ACT = "act", "Акт"
    INVOICE = "invoice", "Счёт"
    LETTER = "letter", "Письмо"
    OTHER = "other", "Другое"


class ProcurementDocument(models.Model):
    """Файл по закупке. Отдаётся только через вьюху с проверкой прав (django-sendfile)."""

    procurement = models.ForeignKey(
        Procurement, on_delete=models.CASCADE, related_name="documents", verbose_name="закупка"
    )
    kind = models.CharField("вид", max_length=10, choices=DocumentKind.choices, default=DocumentKind.OTHER)
    title = models.CharField("название", max_length=200)
    file = models.FileField("файл", upload_to="procurements/%Y/")
    uploaded_by = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="загрузил",
    )
    created_at = models.DateTimeField("загружен", auto_now_add=True)

    class Meta:
        verbose_name = "документ закупки"
        verbose_name_plural = "документы закупки"
        ordering = ["kind", "-created_at"]

    def __str__(self) -> str:
        return self.title


class PassStatus(models.TextChoices):
    NEEDED = "needed", "Нужно заказать"
    REQUESTED = "requested", "Заявка подана"
    ISSUED = "issued", "Оформлен"
    REFUSED = "refused", "Отказано"


class Pass(models.Model):
    """Пропуск на объект заказчика — на человека или на машину.

    Привязан к закупке, а не к командировке: пропуск обычно выписывают на срок
    контракта, и он действует во всех заходах, пока не истёк.
    """

    procurement = models.ForeignKey(
        Procurement, on_delete=models.CASCADE, related_name="passes", verbose_name="закупка"
    )
    employee = models.ForeignKey(
        "core.Employee", on_delete=models.CASCADE, null=True, blank=True,
        related_name="passes", verbose_name="сотрудник",
    )
    vehicle = models.CharField(
        "автомобиль", max_length=80, blank=True,
        help_text="Марка и госномер — если пропуск на машину",
    )
    site = models.ForeignKey(
        "verification.Site", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="passes", verbose_name="объект",
        help_text="Пусто — пропуск на все объекты заказчика",
    )
    number = models.CharField("номер пропуска", max_length=60, blank=True)
    valid_from = models.DateField("действует с", null=True, blank=True)
    valid_to = models.DateField("действует по", null=True, blank=True)
    status = models.CharField("состояние", max_length=10, choices=PassStatus.choices, default=PassStatus.NEEDED)
    note = models.CharField("примечание", max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "пропуск"
        verbose_name_plural = "пропуска"
        ordering = ["employee__full_name", "valid_from"]

    def __str__(self) -> str:
        return f"Пропуск: {self.holder}"

    @property
    def holder(self) -> str:
        return self.employee.full_name if self.employee_id else (self.vehicle or "—")

    def covers(self, date_from: dt.date | None, date_to: dt.date | None) -> bool:
        """Оформлен и действует на весь промежуток. Открытая граница — без ограничения."""
        if self.status != PassStatus.ISSUED:
            return False
        if self.valid_from and date_from and self.valid_from > date_from:
            return False
        if self.valid_to and (date_to or date_from) and self.valid_to < (date_to or date_from):
            return False
        return True


# ---------------------------------------------------------------------------
# Командировка
# ---------------------------------------------------------------------------
class TripKind(models.TextChoices):
    CITY = "city", "Город"
    CLASSIC = "classic", "Командировка"
    PROCUREMENT = "procurement", "По закупке"


class TripStatus(models.TextChoices):
    PLANNED = "planned", "Запланирована"
    ACTIVE = "active", "Идёт"
    DONE = "done", "Завершена"
    CANCELLED = "cancelled", "Отменена"


class Trip(models.Model):
    kind = models.CharField("вид", max_length=12, choices=TripKind.choices, default=TripKind.CLASSIC)
    title = models.CharField("название", max_length=200)
    destination = models.CharField(
        "куда", max_length=200, blank=True, help_text="Район или населённый пункт"
    )
    procurement = models.ForeignKey(
        Procurement, on_delete=models.PROTECT, null=True, blank=True,
        related_name="trips", verbose_name="закупка",
        help_text="Заполнено у командировок по закупке: одна закупка — сколько угодно заходов",
    )
    date_start = models.DateField("выезд", null=True, blank=True)
    date_end = models.DateField("возвращение", null=True, blank=True)
    status = models.CharField("состояние", max_length=10, choices=TripStatus.choices, default=TripStatus.PLANNED)
    lead = models.ForeignKey(
        "core.Employee", on_delete=models.PROTECT, related_name="led_trips", verbose_name="старший"
    )
    members = models.ManyToManyField(
        "core.Employee", blank=True, related_name="trips", verbose_name="участники",
        help_text="Кроме старшего",
    )
    note = models.TextField("примечание", blank=True)
    route = models.JSONField(
        "последний расчёт маршрута", default=dict, blank=True,
        help_text="Откуда, когда выезд, чем считали время в пути, итоги — см. apps.logistics.planner",
    )
    created_at = models.DateTimeField("заведена", auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "выезд"
        verbose_name_plural = "выезды"
        ordering = ["-date_start", "-created_at"]
        indexes = [models.Index(fields=["status", "date_start"])]

    def __str__(self) -> str:
        return self.title

    @property
    def is_open(self) -> bool:
        return self.status in (TripStatus.PLANNED, TripStatus.ACTIVE)

    @property
    def visit_number(self) -> int | None:
        """Какой это заход по закупке (1, 2, …) — по порядку заведения."""
        if not self.procurement_id:
            return None
        return Trip.objects.filter(procurement_id=self.procurement_id, pk__lte=self.pk).count()


class StopStatus(models.TextChoices):
    PLANNED = "planned", "Ждёт"
    DONE = "done", "Выполнен"
    NO_ANSWER = "no_answer", "Не открыли"
    POSTPONED = "postponed", "Перенос"
    REFUSED = "refused", "Отказ"


#: Адрес не отработан и переезжает в следующий заход.
UNFINISHED = (StopStatus.PLANNED, StopStatus.NO_ANSWER, StopStatus.POSTPONED)


class TripStop(models.Model):
    """Адрес командировки. Поверки по нему лежат в наряде (``work_order``)."""

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="stops", verbose_name="командировка")
    position = models.PositiveIntegerField("порядок объезда", default=0)
    site = models.ForeignKey(
        "verification.Site", on_delete=models.PROTECT, related_name="trip_stops", verbose_name="объект"
    )
    client = models.ForeignKey(
        "verification.Client", on_delete=models.PROTECT, related_name="trip_stops", verbose_name="клиент"
    )
    work_order = models.OneToOneField(
        "verification.WorkOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="trip_stop", verbose_name="наряд",
    )
    status = models.CharField("состояние", max_length=10, choices=StopStatus.choices, default=StopStatus.PLANNED)
    planned_date = models.DateField("день", null=True, blank=True)
    planned_count = models.PositiveSmallIntegerField(
        "ожидается приборов", null=True, blank=True
    )
    contact_name = models.CharField("кто встречает", max_length=160, blank=True)
    contact_phone = models.CharField("телефон", max_length=32, blank=True)
    note = models.CharField("заметка", max_length=300, blank=True)
    window_start = models.TimeField("ждут с", null=True, blank=True)
    window_end = models.TimeField("ждут до", null=True, blank=True)
    service_minutes = models.PositiveSmallIntegerField(
        "минут на адрес", null=True, blank=True, help_text="Пусто — по умолчанию из расчёта маршрута",
    )
    eta = models.DateTimeField("приезд по плану", null=True, blank=True)
    drive_seconds = models.PositiveIntegerField("ехать от предыдущего, с", null=True, blank=True)
    added_on_site = models.BooleanField(
        "добавлен на месте", default=False,
        help_text="Не из плана: клиент появился уже в командировке",
    )
    carried_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="carried_to",
        verbose_name="перенесён из", help_text="Адрес прошлого захода, который не успели отработать",
    )
    visited_at = models.DateTimeField("отмечен", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "адрес командировки"
        verbose_name_plural = "адреса командировки"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"{self.trip}: {self.site}"
