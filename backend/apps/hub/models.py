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

    si_description = models.TextField("что нужно поверить", blank=True)
    desired_date = models.DateField("желаемая дата", null=True, blank=True)
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
