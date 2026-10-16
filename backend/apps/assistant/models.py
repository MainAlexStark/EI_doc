"""Ассистент-метролог: чаты, контекст (файлы компании, память), действия на подтверждение.

Как устроено (подробно — claude/assistant.md в проекте):

* ``Conversation`` / ``Message`` — история чата в том же виде, в каком она
  уходит в LLM (OpenAI-совместимый формат: user / assistant с tool_calls /
  tool с результатом). Поле ``ui`` — то, что видит человек сверх текста:
  виджеты, источники, карточки действий. LLM его не получает.
* ``Run`` — один прогон агента (ответ на сообщение). Работает в Celery,
  пишет готовые сообщения в БД, а недописанный текст — в кэш; экран чата
  опрашивает ``/runs/<id>/`` и показывает текст по мере генерации.
* ``KnowledgeFile`` / ``KnowledgeChunk`` — «Контекст»: внутренние документы,
  правила, методики. Режим ``always`` — файл целиком в системном промте,
  ``search`` — агент находит нужные куски поиском, ``off`` — выключен.
* ``MemoryItem`` — постоянная память: факты о компании (общие) и личные
  заметки сотрудника, которые агент учитывает в каждом чате.
* ``PendingAction`` — всё, что меняет данные (завести тип СИ, записать в
  общую память), агент только готовит; выполняется по кнопке человека.
* ``McpServer`` — внешние MCP-серверы (RSTMetrology_MCP и другие).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class ContextMode(models.TextChoices):
    ALWAYS = "always", "Всегда в контексте"
    SEARCH = "search", "По поиску"
    OFF = "off", "Выключен"


class Scope(models.TextChoices):
    COMPANY = "company", "Общее для компании"
    PERSONAL = "personal", "Личное"


class KnowledgeCategory(models.TextChoices):
    RULES = "rules", "Правила и регламенты компании"
    METHODS = "methods", "Методики поверки"
    TYPES = "types", "Описания типов СИ"
    STANDARDS = "standards", "Эталоны и свидетельства"
    TEMPLATES = "templates", "Образцы протоколов и документов"
    NORMATIVE = "normative", "ГОСТ, приказы, нормативка"
    OTHER = "other", "Прочее"


class AssistantSettings(models.Model):
    """Одна действующая запись: модель по умолчанию и дополнение к системному промту."""

    default_model = models.CharField(
        "модель по умолчанию", max_length=200, blank=True,
        help_text="Например openrouter:anthropic/claude-sonnet-4.5. Пусто — из ASSISTANT_DEFAULT_MODEL",
    )
    extra_instructions = models.TextField(
        "дополнительные правила компании для ассистента", blank=True,
        help_text="Добавляется к системному промту в каждом чате",
    )
    max_steps = models.PositiveSmallIntegerField(
        "шагов агента на один ответ", default=30,
        help_text="Сколько раз подряд агент может вызвать инструменты, прежде чем обязан ответить",
    )
    always_budget_chars = models.PositiveIntegerField(
        "бюджет файлов «всегда в контексте», символов", default=120_000,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "настройки ассистента"
        verbose_name_plural = "настройки ассистента"

    def __str__(self) -> str:
        return "Настройки ассистента"

    @classmethod
    def current(cls) -> "AssistantSettings":
        obj = cls.objects.order_by("pk").first()
        return obj or cls.objects.create()


class McpServer(models.Model):
    """Внешний MCP-сервер (Streamable HTTP)."""

    name = models.SlugField(
        "короткое имя", max_length=32, unique=True,
        help_text="Латиница, без пробелов: rst. Попадает в имена инструментов: mcp__rst__search_types",
    )
    title = models.CharField("название", max_length=120, blank=True)
    url = models.URLField("адрес /mcp", max_length=400)
    headers = models.JSONField(
        "заголовки", default=dict, blank=True,
        help_text='Например {"Authorization": "Bearer …"}. Не показываются в интерфейсе',
    )
    description = models.TextField(
        "когда использовать", blank=True,
        help_text="Подсказка агенту: для чего этот сервер",
    )
    enabled = models.BooleanField("включён", default=True)
    disabled_tools = models.JSONField("выключенные инструменты", default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "MCP-сервер"
        verbose_name_plural = "MCP-серверы"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.title or self.name


class Attachment(models.Model):
    """Файл в чате: загруженный человеком или созданный агентом."""

    class Origin(models.TextChoices):
        USER = "user", "Загружен"
        AGENT = "agent", "Создан ассистентом"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    conversation = models.ForeignKey(
        "Conversation", on_delete=models.CASCADE, null=True, blank=True, related_name="attachments",
    )
    origin = models.CharField(max_length=8, choices=Origin.choices, default=Origin.USER)
    file = models.FileField(upload_to="assistant/%Y/%m/")
    name = models.CharField(max_length=255)
    mime = models.CharField(max_length=120, blank=True)
    size = models.PositiveIntegerField(default=0)
    text = models.TextField("извлечённый текст", blank=True)
    extract_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "файл чата"
        verbose_name_plural = "файлы чата"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")


class Conversation(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_conversations",
    )
    title = models.CharField(max_length=200, blank=True)
    model = models.CharField(max_length=200, blank=True)
    pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        verbose_name = "чат с ассистентом"
        verbose_name_plural = "чаты с ассистентом"
        ordering = ["-pinned", "-updated_at"]

    def __str__(self) -> str:
        return self.title or f"Чат {self.pk}"


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Человек"
        ASSISTANT = "assistant", "Ассистент"
        TOOL = "tool", "Результат инструмента"
        EVENT = "event", "Событие"  # подтверждение действия и т. п. — LLM видит как заметку

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    run = models.ForeignKey("Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField(blank=True)
    tool_calls = models.JSONField(default=list, blank=True)
    tool_call_id = models.CharField(max_length=120, blank=True)
    tool_name = models.CharField(max_length=120, blank=True)
    ui = models.JSONField(
        default=dict, blank=True,
        help_text="То, что видит человек: widgets, sources, action, summary. В LLM не уходит",
    )
    attachments = models.ManyToManyField(Attachment, blank=True, related_name="messages")
    model = models.CharField(max_length=200, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:40]}"


class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Работает"
        CANCELLING = "cancelling", "Останавливается"
        DONE = "done", "Готово"
        CANCELLED = "cancelled", "Остановлен"
        ERROR = "error", "Ошибка"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    model = models.CharField(max_length=200)
    error = models.TextField(blank=True)
    activity = models.CharField("что делает сейчас", max_length=300, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-pk"]

    @property
    def is_active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING, self.Status.CANCELLING)


class KnowledgeFile(models.Model):
    title = models.CharField("название", max_length=250)
    category = models.CharField(
        "раздел", max_length=12, choices=KnowledgeCategory.choices, default=KnowledgeCategory.OTHER,
    )
    description = models.TextField(
        "о чём файл", blank=True,
        help_text="Одна-две строки: агент по ним решает, когда этот файл нужен",
    )
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.COMPANY)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Для личных файлов — чей",
    )
    mode = models.CharField(max_length=8, choices=ContextMode.choices, default=ContextMode.SEARCH)
    file = models.FileField(upload_to="knowledge/%Y/", blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    mime = models.CharField(max_length=120, blank=True)
    text = models.TextField("текст", blank=True)
    chars = models.PositiveIntegerField(default=0)
    extract_error = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "файл контекста"
        verbose_name_plural = "файлы контекста"
        ordering = ["category", "title"]

    def __str__(self) -> str:
        return self.title


class KnowledgeChunk(models.Model):
    file = models.ForeignKey(KnowledgeFile, on_delete=models.CASCADE, related_name="chunks")
    index = models.PositiveIntegerField()
    heading = models.CharField(max_length=300, blank=True)
    text = models.TextField()

    class Meta:
        ordering = ["file", "index"]
        constraints = [
            models.UniqueConstraint(fields=["file", "index"], name="knowledgechunk_unique_index")
        ]


class MemoryItem(models.Model):
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.PERSONAL)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+",
        help_text="Для личной памяти — чья",
    )
    text = models.TextField()
    source_conversation = models.ForeignKey(
        Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "запись памяти"
        verbose_name_plural = "память ассистента"
        ordering = ["scope", "pk"]

    def __str__(self) -> str:
        return self.text[:60]


class PendingAction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ждёт подтверждения"
        DONE = "done", "Выполнено"
        REJECTED = "rejected", "Отклонено"
        FAILED = "failed", "Не удалось"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="actions")
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=250)
    payload = models.JSONField(default=dict)
    sources = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]

    def __str__(self) -> str:
        return self.title
