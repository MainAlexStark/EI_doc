"""ФГИС «Аршин»: кэш документов утверждённых типов СИ и отправка результатов поверки.

Порядок отправки — docs/arshin.md.
"""

from __future__ import annotations

from django.db import models


class FifDocument(models.Model):
    """Файл из карточки утверждённого типа: методика поверки или описание типа.

    Привязан к типу в Госреестре (mit_uuid), а не к нашему SiType: один тип
    реестра — это часто несколько наших типов-модификаций (СГВ-15, СГВ-20…),
    и методика у них общая. Файл скачивается к себе, чтобы открываться и тогда,
    когда ФГИС недоступна или не отвечает нероссийскому адресу.
    """

    class Kind(models.TextChoices):
        METHOD = "meth", "Методика поверки"
        DESCRIPTION = "spec", "Описание типа"

    mit_uuid = models.CharField("тип в реестре (mit_uuid)", max_length=64, db_index=True)
    registry_number = models.CharField("номер в Госреестре", max_length=40, blank=True)
    doc_uuid = models.CharField("документ в ФГИС", max_length=64, unique=True)
    kind = models.CharField("вид", max_length=8, choices=Kind.choices)
    title = models.CharField("название", max_length=255)
    url = models.URLField("ссылка в ФГИС", max_length=500)
    file = models.FileField("файл", upload_to="fif_docs/", blank=True)
    filename = models.CharField("имя файла", max_length=255, blank=True)
    content_type = models.CharField("тип содержимого", max_length=100, blank=True)
    size = models.PositiveIntegerField("размер, байт", null=True, blank=True)
    downloaded_at = models.DateTimeField("скачан", null=True, blank=True)
    error = models.CharField("ошибка скачивания", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "документ типа СИ из ФГИС"
        verbose_name_plural = "документы типов СИ из ФГИС"
        ordering = ["kind", "title"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.registry_number or self.mit_uuid}"


# ---------------------------------------------------------------------------
# Отправка результатов поверки
# ---------------------------------------------------------------------------
class SubmissionStatus(models.TextChoices):
    GENERATED = "generated", "XML сформирован"
    UPLOADED = "uploaded", "Загружен в ЛК ФИФ"
    DONE = "done", "Сверен с ФИФ"
    CANCELLED = "cancelled", "Отменён"


class ItemStatus(models.TextChoices):
    PENDING = "pending", "В пакете"
    SENT = "sent", "Отправлен"
    ACCEPTED = "accepted", "Опубликован"
    REJECTED = "rejected", "Отклонён"

    @classmethod
    def active(cls) -> tuple[str, ...]:
        """Строки, которые держат протокол: в другой пакет он не попадёт."""
        return (cls.PENDING, cls.SENT, cls.ACCEPTED)


class ArshinSubmission(models.Model):
    """Пакет результатов поверки для ФГИС «Аршин».

    Запись в ФИФ идёт пакетным XML через личный кабинет — пакет формируется
    здесь, загружается человеком, затем сверяется с открытым реестром.
    Идемпотентность: один протокол — не больше одной действующей строки во
    всех пакетах (см. ограничение у ArshinSubmissionItem). Отклонённая строка
    протокол отпускает — его можно отправить в следующем пакете.
    """

    status = models.CharField("состояние", max_length=12, choices=SubmissionStatus.choices,
                              default=SubmissionStatus.GENERATED)
    xml = models.FileField("XML", upload_to="arshin/%Y/%m/", blank=True)
    sha256 = models.CharField("SHA-256 XML", max_length=64, blank=True)
    created_by = models.ForeignKey(
        "core.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="arshin_submissions", verbose_name="сформировал",
    )
    created_at = models.DateTimeField("сформирован", auto_now_add=True)
    uploaded_at = models.DateTimeField("загружен в ЛК", null=True, blank=True)
    reconciled_at = models.DateTimeField("последняя сверка", null=True, blank=True)
    attempts = models.PositiveSmallIntegerField("попыток сверки", default=0)
    note = models.TextField("заметка", blank=True)

    class Meta:
        verbose_name = "пакет в Аршин"
        verbose_name_plural = "пакеты в Аршин"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Пакет №{self.pk} от {self.created_at:%d.%m.%Y}"


class ArshinSubmissionItem(models.Model):
    submission = models.ForeignKey(
        ArshinSubmission, on_delete=models.CASCADE, related_name="items", verbose_name="пакет"
    )
    protocol = models.ForeignKey(
        "verification.Protocol", on_delete=models.PROTECT, related_name="arshin_items",
        verbose_name="протокол",
    )
    status = models.CharField("состояние", max_length=10, choices=ItemStatus.choices,
                              default=ItemStatus.PENDING)
    fif_record_number = models.CharField("номер записи в ФИФ", max_length=60, blank=True)
    error = models.TextField("причина отказа", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "строка пакета в Аршин"
        verbose_name_plural = "строки пакетов в Аршин"
        ordering = ["submission", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["protocol"],
                condition=models.Q(status__in=["pending", "sent", "accepted"]),
                name="arshin_item_one_active_per_protocol",
            )
        ]

    def __str__(self) -> str:
        return f"{self.protocol} · {self.get_status_display()}"
