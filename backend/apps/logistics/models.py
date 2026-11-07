"""Командировки и маршруты (этап 4, docs/logistics.md).

Командировка — выезд одного или нескольких поверителей на даты с набором
нарядов (остановок) и забронированными эталонами. План — остановки в порядке
маршрута; факт — что по ним закрыто (наряд закрыт, поверки приняты).

Координаты — обычные широта/долгота у объекта (verification.Site) и заявки;
расстояния считаются по OSRM, если он поднят, иначе по прямой с поправкой
на дороги. PostGIS для этого не нужен.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models


class TripStatus(models.TextChoices):
    PLANNED = "planned", "Планируется"
    CONFIRMED = "confirmed", "Утверждена"
    IN_PROGRESS = "in_progress", "В пути"
    DONE = "done", "Завершена"
    CANCELLED = "cancelled", "Отменена"


class Trip(models.Model):
    title = models.CharField("название", max_length=200)
    district = models.ForeignKey(
        "catalog.District", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="trips", verbose_name="район",
    )
    date_from = models.DateField("с")
    date_to = models.DateField("по")
    status = models.CharField("состояние", max_length=12, choices=TripStatus.choices,
                              default=TripStatus.PLANNED)
    employees = models.ManyToManyField("core.Employee", related_name="trips", verbose_name="поверители")
    start_address = models.CharField(
        "откуда выезд", max_length=350, blank=True,
        help_text="Пусто — от офиса (LOGISTICS_DEPOT_* в настройках)",
    )
    start_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    start_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    vehicle = models.CharField("транспорт", max_length=120, blank=True)
    budget = models.DecimalField("смета, ₽", max_digits=10, decimal_places=2, null=True, blank=True)
    actual_cost = models.DecimalField("фактические расходы, ₽", max_digits=10, decimal_places=2,
                                      null=True, blank=True)
    note = models.TextField("примечание", blank=True)
    created_by = models.ForeignKey(
        "core.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="создал",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "командировка"
        verbose_name_plural = "командировки"
        ordering = ["-date_from", "-pk"]

    def __str__(self) -> str:
        return f"{self.title} ({self.date_from:%d.%m}–{self.date_to:%d.%m.%Y})"

    def clean(self) -> None:
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError({"date_to": "Командировка заканчивается раньше, чем начинается"})


class StopStatus(models.TextChoices):
    PLANNED = "planned", "По плану"
    DONE = "done", "Выполнена"
    SKIPPED = "skipped", "Не состоялась"


class TripStop(models.Model):
    """Остановка маршрута — обычно наряд на объект."""

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="stops", verbose_name="командировка")
    work_order = models.OneToOneField(
        "verification.WorkOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="trip_stop", verbose_name="наряд",
    )
    address = models.CharField("адрес", max_length=350)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    day = models.DateField("день", null=True, blank=True)
    position = models.PositiveSmallIntegerField("порядок", default=0)
    window_from = models.TimeField("окно с", null=True, blank=True)
    window_to = models.TimeField("окно до", null=True, blank=True)
    service_minutes = models.PositiveSmallIntegerField("минут на объекте", default=30)
    eta = models.TimeField("расчётное прибытие", null=True, blank=True)
    status = models.CharField("состояние", max_length=10, choices=StopStatus.choices,
                              default=StopStatus.PLANNED)
    note = models.CharField("примечание", max_length=250, blank=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        verbose_name = "остановка"
        verbose_name_plural = "остановки"
        ordering = ["trip", "day", "position", "pk"]

    def __str__(self) -> str:
        return f"{self.position}. {self.address}"

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class StandardReservation(models.Model):
    """Эталон уезжает с командировкой — в эти дни им больше никто не пользуется."""

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="reservations",
                             verbose_name="командировка")
    standard = models.ForeignKey("catalog.Standard", on_delete=models.PROTECT, related_name="reservations",
                                 verbose_name="эталон")
    date_from = models.DateField("с")
    date_to = models.DateField("по")

    class Meta:
        verbose_name = "бронь эталона"
        verbose_name_plural = "брони эталонов"
        ordering = ["date_from"]
        constraints = [
            models.UniqueConstraint(fields=["trip", "standard"], name="reservation_unique_trip_standard"),
        ]

    def __str__(self) -> str:
        return f"{self.standard} → {self.trip}"

    def overlapping(self):
        """Другие брони этого эталона на пересекающиеся даты (кроме отменённых командировок)."""
        return (
            StandardReservation.objects.filter(
                standard=self.standard, date_from__lte=self.date_to, date_to__gte=self.date_from
            )
            .exclude(pk=self.pk)
            .exclude(trip__status=TripStatus.CANCELLED)
        )

    def clean(self) -> None:
        if self.date_to < self.date_from:
            raise ValidationError("Бронь заканчивается раньше, чем начинается")
        clash = self.overlapping().select_related("trip").first()
        if clash:
            raise ValidationError(f"Эталон уже забронирован: {clash.trip}")
        standard = self.standard
        if standard.valid_until and standard.valid_until < self.date_to:
            raise ValidationError(
                f"Поверка эталона действует до {standard.valid_until:%d.%m.%Y} — раньше конца командировки"
            )
