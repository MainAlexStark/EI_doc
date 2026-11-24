from __future__ import annotations

from django.db import models


class Folder(models.TextChoices):
    INBOX = "inbox", "Входящие"
    SENT = "sent", "Отправленные"
    ARCHIVE = "archive", "Архив"
    JUNK = "junk", "Спам"
    TRASH = "trash", "Корзина"


class Direction(models.TextChoices):
    IN = "in", "Входящее"
    OUT = "out", "Исходящее"


class FolderState(models.Model):
    """Где остановилась синхронизация папки: UIDVALIDITY и последний UID."""

    box = models.CharField("ящик", max_length=32)
    folder = models.CharField("папка", max_length=8, choices=Folder.choices)
    imap_name = models.CharField("имя на сервере", max_length=255, blank=True)
    uidvalidity = models.BigIntegerField(null=True, blank=True)
    last_uid = models.BigIntegerField(default=0)
    # Первая загрузка идёт пачками по несколько тиков; пока она не закончена,
    # «новые» письма — это старая почта, о ней не уведомляем.
    initial_done = models.BooleanField(default=False)
    synced_at = models.DateTimeField("синхронизирована", null=True, blank=True)
    error = models.TextField("ошибка", blank=True)

    class Meta:
        verbose_name = "папка ящика"
        verbose_name_plural = "папки ящиков"
        constraints = [models.UniqueConstraint(fields=["box", "folder"], name="mailbox_folder_unique")]

    def __str__(self) -> str:
        return f"{self.box}/{self.folder}"


def raw_path(instance: "Message", filename: str) -> str:
    return f"mailbox/{instance.box}/{instance.created_at:%Y/%m}/{filename}"


class Message(models.Model):
    """Письмо из ящика (или наше исходящее). Исходник .eml — в MEDIA, вложения
    достаются из него по номеру части (как inbox.Part в openseller)."""

    box = models.CharField("ящик", max_length=32, db_index=True)
    folder = models.CharField("папка", max_length=8, choices=Folder.choices, default=Folder.INBOX)
    uid = models.BigIntegerField(null=True, blank=True)
    uidvalidity = models.BigIntegerField(null=True, blank=True)
    direction = models.CharField(max_length=3, choices=Direction.choices, default=Direction.IN)
    thread = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    message_id = models.CharField(max_length=500, blank=True, db_index=True)
    # Для писем без Message-ID: хэш даты, отправителя, темы и размера — узнать то же письмо в другой папке.
    fingerprint = models.CharField(max_length=40, blank=True, db_index=True)
    # Письмо пропало из папки на сервере (перенесли/удалили) или мы сами его переносим:
    # строку держим, пока оно не найдётся в другой папке, потом — удаляем (sync.purge_detached).
    detached_at = models.DateTimeField(null=True, blank=True)
    in_reply_to = models.CharField(max_length=500, blank=True)
    refs = models.TextField(blank=True)
    from_addr = models.CharField("от", max_length=320, blank=True)
    from_name = models.CharField(max_length=300, blank=True)
    to_addrs = models.TextField("кому", blank=True)
    cc_addrs = models.TextField("копия", blank=True)
    reply_to = models.CharField(max_length=320, blank=True)
    subject = models.CharField("тема", max_length=1000, blank=True)
    sent_at = models.DateTimeField("дата письма", null=True, blank=True)
    received_at = models.DateTimeField("получено", db_index=True)
    preview = models.CharField(max_length=300, blank=True)
    text = models.TextField(blank=True)
    html = models.TextField("HTML (очищенный)", blank=True)
    remote_images = models.BooleanField(default=False)
    attachments = models.JSONField(default=list, blank=True)
    size = models.PositiveIntegerField(default=0)
    too_big = models.BooleanField(default=False)
    raw = models.FileField(upload_to=raw_path, max_length=255, blank=True)
    seen = models.BooleanField(default=False)
    flagged = models.BooleanField(default=False)
    # Флаги поменяли у нас, на сервер ещё не ушли (push_flag или ближайшая синхронизация).
    flags_dirty = models.BooleanField(default=False)
    outbox = models.ForeignKey(
        "notify.OutboxMail", on_delete=models.SET_NULL, null=True, blank=True, related_name="mailbox_messages",
    )
    sent_by = models.ForeignKey(
        "core.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="отправил",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "письмо ящика"
        verbose_name_plural = "почта фирмы"
        ordering = ["-received_at", "-id"]
        indexes = [models.Index(fields=["box", "folder", "-received_at"], name="mailbox_list")]
        constraints = [
            models.UniqueConstraint(
                fields=["box", "folder", "uidvalidity", "uid"], name="mailbox_uid_unique",
                condition=models.Q(uid__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return self.subject or "(без темы)"

    @property
    def thread_root_id(self) -> int:
        return self.thread_id or self.pk

    @property
    def unread(self) -> bool:
        return self.direction == Direction.IN and not self.seen
