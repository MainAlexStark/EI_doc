"""ФГИС «Аршин»: кэш документов утверждённых типов СИ.

Отправка результатов поверки — следующие этапы (см. claude/architecture.md).
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
