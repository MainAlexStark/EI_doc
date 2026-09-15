"""Заявки с сайта EI_Hub.

Отдельный сервис EI_Hub переезжает сюда: одна база, один деплой. Путь заявки:

    форма на сайте -> авто-роутинг по району -> подтверждение диспетчером ->
    наряд (apps.verification.WorkOrder) -> поверка -> закрытие

Район заявке даёт не геокодирование (оно только с этапа 4), а структурированный
ответ подсказки адреса DaData — см. apps.hub.address.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class RequestSource(models.TextChoices):
    SITE = "site", "Форма на сайте"
    PHONE = "phone", "Телефон"
    EMAIL = "email", "Email"
    MANUAL = "manual", "Внесена вручную"


class RequestStatus(models.TextChoices):
    NEW = "new", "Новая"
    ROUTED = "routed", "Подобран исполнитель"
    CONFIRMED = "confirmed", "Подтверждена"  # наряд создан
    REJECTED = "rejected", "Отклонена"
    SPAM = "spam", "Спам / дубль"


class DistrictAssignment(models.Model):
    """Кто по умолчанию берёт заявки из района.

    На район может быть закреплено несколько сотрудников — тогда роутинг
    предлагает того, у кого меньше `priority`. Это рекомендация, не приказ:
    диспетчер всегда может назначить другого при подтверждении заявки.
    """

    district = models.ForeignKey(
        "catalog.District", on_delete=models.CASCADE, related_name="assignments",
        verbose_name="район",
    )
    employee = models.ForeignKey(
        "core.Employee", on_delete=models.CASCADE, related_name="district_assignments",
        verbose_name="сотрудник",
    )
    priority = models.PositiveSmallIntegerField(
        "приоритет", default=100, help_text="Меньше — выше в очереди на назначение",
    )
    is_active = models.BooleanField("действует", default=True)

    class Meta:
        verbose_name = "закрепление района"
        verbose_name_plural = "закрепления районов"
        ordering = ["district", "priority"]
        constraints = [
            models.UniqueConstraint(
                fields=["district", "employee"], name="districtassignment_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.district} → {self.employee}"


class AvailabilityKind(models.TextChoices):
    DISTRICT = "district", "Выезд в своём районе"
    TRIP = "trip", "Командировка"


class EmployeeAvailability(models.Model):
    """Слот, когда сотрудник может выезжать — заводит сам сотрудник в своём календаре.

    Два независимых смысла в одной модели:

    * `kind` — куда сотрудник готов выезжать (в своём районе или в командировку);
    * `is_priority` — это время сотрудник отмечает как предпочтительное для себя;
      заявителю на форме такой слот показывается со скидкой
      (apps.catalog.models.PricingSettings.priority_discount_percent) — стимул
      выбрать время, которое сотруднику удобнее, а не только клиенту.

    Время (`start_time`/`end_time`) обязательно только для слотов, которые
    реально предлагаются заявителю с выбором времени (счётчики — см.
    MeasurementFamily.requires_time_slot); для остальных типов СИ на форме
    учитывается только дата, независимо от времени слота.
    """

    employee = models.ForeignKey(
        "core.Employee", on_delete=models.CASCADE, related_name="availability_slots",
        verbose_name="сотрудник",
    )
    kind = models.CharField(
        "тип", max_length=10, choices=AvailabilityKind.choices, default=AvailabilityKind.DISTRICT
    )
    date = models.DateField("дата", db_index=True)
    start_time = models.TimeField("с", null=True, blank=True, help_text="Пусто — весь день")
    end_time = models.TimeField("по", null=True, blank=True, help_text="Пусто — весь день")
    is_priority = models.BooleanField(
        "приоритетное время", default=False,
        help_text="Показывается заявителю со скидкой — это время сотруднику удобнее",
    )
    note = models.CharField("примечание", max_length=200, blank=True)

    created_at = models.DateTimeField("создано", auto_now_add=True)

    class Meta:
        verbose_name = "слот доступности сотрудника"
        verbose_name_plural = "слоты доступности сотрудников"
        ordering = ["date", "start_time"]
        indexes = [models.Index(fields=["employee", "date"])]

    def __str__(self) -> str:
        when = self.date.strftime("%d.%m.%Y")
        if self.start_time and self.end_time:
            when += f", {self.start_time:%H:%M}–{self.end_time:%H:%M}"
        return f"{self.employee} — {when}"

    def covers(self, *, at_time) -> bool:
        """Слот без времени покрывает весь день; со временем — только свой интервал."""
        if at_time is None or self.start_time is None or self.end_time is None:
            return True
        return self.start_time <= at_time <= self.end_time


class Request(models.Model):
    """Заявка на поверку — с сайта, по телефону или занесённая вручную.

    К Client и Site не привязываем при создании: заявитель может оказаться
    дублем существующего клиента, а сам адрес — уже заведённым объектом.
    Это решает диспетчер на подтверждении, не форма на сайте.
    """

    source = models.CharField(
        "источник", max_length=10, choices=RequestSource.choices, default=RequestSource.SITE
    )
    status = models.CharField(
        "статус", max_length=10, choices=RequestStatus.choices, default=RequestStatus.NEW
    )

    contact_name = models.CharField("имя / организация", max_length=200)
    contact_phone = models.CharField("телефон", max_length=32, blank=True)
    contact_email = models.EmailField("email", blank=True)

    # Адрес — из подсказки DaData (apps.hub.address) или введён вручную,
    # если подсказка не настроена или не нашла вариант.
    address = models.CharField("адрес", max_length=350)
    postal_code = models.CharField("индекс", max_length=6, blank=True)
    fias_id = models.CharField("ФИАС ID", max_length=64, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    district = models.ForeignKey(
        "catalog.District", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="requests", verbose_name="район",
    )
    is_address_confirmed = models.BooleanField(
        "адрес выбран из подсказки", default=False,
        help_text="Снято — заявитель ввёл адрес вручную, район не распознан автоматически",
    )

    si_description = models.TextField(
        "что нужно поверить — доп. примечание", blank=True,
        help_text="Список приборов теперь — RequestItem (family + количество); "
                  "здесь только свободный текст, если заявитель что-то уточнил словами",
    )
    desired_date = models.DateField("желаемая дата", null=True, blank=True)
    desired_time = models.TimeField(
        "желаемое время", null=True, blank=True,
        help_text="Заполняется, только если среди приборов заявки есть хотя бы один, "
                  "требующий выбора времени (MeasurementFamily.requires_time_slot)",
    )
    is_priority_slot = models.BooleanField(
        "выбран приоритетный слот", default=False,
        help_text="Заявитель выбрал дату/время, отмеченные сотрудником как приоритетные "
                  "(apps.hub.models.EmployeeAvailability.is_priority) — даёт скидку",
    )
    estimated_price = models.DecimalField(
        "примерная цена", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Снимок расчёта на момент отправки формы — по текущим ценам может "
                  "отличаться, это ориентир заявителю, а не выставленный счёт",
    )
    discount_percent = models.DecimalField(
        "применённая скидка, %", max_digits=4, decimal_places=1, default=0,
        help_text="Снимок PricingSettings.priority_discount_percent на момент отправки",
    )
    comment = models.TextField("комментарий", blank=True)

    suggested_employee = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="suggested_requests", verbose_name="рекомендованный исполнитель",
    )
    assigned_employee = models.ForeignKey(
        "core.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assigned_requests", verbose_name="назначенный исполнитель",
    )
    reject_reason = models.CharField("причина отклонения", max_length=250, blank=True)

    ip_address = models.GenericIPAddressField("IP заявителя", null=True, blank=True)
    honeypot_tripped = models.BooleanField(
        "поймана honeypot-полем", default=False,
        help_text="Заявка всё равно сохраняется — так проще смотреть, сколько спама отсеивается",
    )

    created_at = models.DateTimeField("создана", auto_now_add=True, db_index=True)
    routed_at = models.DateTimeField("подобран исполнитель", null=True, blank=True)
    confirmed_at = models.DateTimeField("подтверждена", null=True, blank=True)

    class Meta:
        verbose_name = "заявка"
        verbose_name_plural = "заявки"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"Заявка №{self.pk or '—'} от {self.contact_name}"

    def mark_confirmed(self, *, employee) -> None:
        self.status = RequestStatus.CONFIRMED
        self.confirmed_at = timezone.now()
        self.assigned_employee = employee
        self.save(update_fields=["status", "confirmed_at", "assigned_employee"])


class RequestItem(models.Model):
    """Одна строка «что поверить» в заявке — семейство СИ + количество.

    Семейство, не конкретный SiType из Госреестра: на форме заявитель выбирает
    из короткого списка (счётчики воды, манометры, весы...), точную модель
    прибора устанавливает поверитель уже на месте.
    """

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="items", verbose_name="заявка")
    family = models.ForeignKey(
        "catalog.MeasurementFamily", on_delete=models.PROTECT, related_name="+", verbose_name="тип прибора"
    )
    quantity = models.PositiveSmallIntegerField("количество", default=1)
    unit_price = models.DecimalField(
        "цена за единицу на момент заявки", max_digits=9, decimal_places=2, default=0,
        help_text="Снимок MeasurementFamily.price на момент отправки формы",
    )

    class Meta:
        verbose_name = "прибор в заявке"
        verbose_name_plural = "приборы в заявке"

    def __str__(self) -> str:
        return f"{self.family} × {self.quantity}"

    @property
    def subtotal(self):
        return self.unit_price * self.quantity
