"""Метрологические характеристики счётчика воды из описания типа (PDF из ФГИС).

Расходы и пределы погрешности есть только в описании типа — в карточке
реестра их нет. Описание читает та же модель Yandex AI Studio, что и
бумажные бланки (apps.hub.yandex_vision): текстовый PDF уходит текстом
нужных страниц, сканированный — картинками первых страниц.

Модель — не источник истины. Значения попадают в форму нового типа как
предложение: человек видит их рядом с цитатой из описания, может поправить,
а метролог потом проверяет тип (SiType.needs_review), как и при ручном вводе.
"""

from __future__ import annotations

import io
import logging
import re

from apps.arshin import documents
from apps.arshin.fif import FifCard
from apps.arshin.models import FifDocument
from apps.hub.yandex_vision import RecognitionUnavailable, YandexVisionClient

logger = logging.getLogger(__name__)

LIMIT_KEYS = (
    "q_min", "q_transition_a", "q_transition_b", "q_nominal", "q_max",
    "error_below_transition", "error_above_transition",
)

TEXT_MIN_CHARS = 400        # меньше — считаем PDF сканом
TEXT_MAX_CHARS = 24000
MAX_PAGES_AS_IMAGES = 6
KEYWORDS = re.compile(r"расход|м\s*3\s*/\s*ч|м³/ч|Q\s*min|Qmin|Q\s*наим|Q\s*п|погрешност|Q\s*[1-4]\b", re.I)

_number = {"type": ["number", "null"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean", "description": "В документе есть таблица расходов для нужной модификации"},
        "variant_matched": {"type": ["string", "null"], "description": "Модификация/Ду, для которой взяты значения"},
        "dn": {"type": ["integer", "null"], "description": "Диаметр условного прохода, мм"},
        "q_min": {**_number, "description": "Наименьший расход Qmin (Q1), м3/ч, для класса В"},
        "q_transition_b": {**_number, "description": "Переходный расход Qt (Q2) для класса В, м3/ч"},
        "q_transition_a": {**_number, "description": "Переходный расход Qt для класса А, м3/ч"},
        "q_nominal": {**_number, "description": "Номинальный расход Qn (Q3), м3/ч"},
        "q_max": {**_number, "description": "Наибольший расход Qmax (Q4), м3/ч"},
        "error_below_transition": {**_number, "description": "Предел погрешности в диапазоне от Qmin до Qt, ±%"},
        "error_above_transition": {**_number, "description": "Предел погрешности от Qt до Qmax для холодной воды, ±%"},
        "quote": {"type": ["string", "null"], "description": "Дословный фрагмент документа, откуда взяты значения"},
        "comment": {"type": ["string", "null"], "description": "Что пришлось допустить или что неоднозначно"},
    },
    "required": ["found"],
}

PROMPT = (
    "Перед тобой описание утверждённого типа средства измерений из Госреестра — "
    "счётчика воды. Найди метрологические характеристики для модификации «{variant}» "
    "(если модификаций несколько и различаются Ду — бери строку, подходящую к ней; "
    "Ду часто зашифрован числом в обозначении: 15, 20, 25…). Нужны: наименьший расход "
    "Qmin (он же Q1), переходный расход Qt (Q2) отдельно для метрологических классов А и В, "
    "номинальный расход Qn (Q3), наибольший расход Qmax (Q4) — все в м³/ч, и пределы "
    "допускаемой относительной погрешности: в диапазоне от Qmin до Qt и от Qt до Qmax "
    "(для холодной воды). Если в документе один переходный расход без деления на классы — "
    "поставь его в оба класса и напиши об этом в comment. Если расходы даны в л/ч — переведи "
    "в м³/ч. Если Qmin зависит от положения установки — бери для горизонтальной установки и "
    "класса В. Ничего не придумывай: чего нет в документе — null, а found=false, если "
    "таблицы расходов нет совсем. В quote дословно процитируй строку или ячейки таблицы, "
    "откуда взял числа."
)


class LimitsUnavailable(RuntimeError):
    """Прочитать характеристики не вышло — человек выбирает шаблон по Ду или вводит вручную."""


def _pdf_parts(content: bytes) -> tuple[str, list[dict]]:
    """Части сообщения для модели: ('text', [...]) или ('images', [...])."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover — зависимость в requirements.txt
        raise LimitsUnavailable("На сервере не установлен pypdfium2") from exc

    try:
        pdf = pdfium.PdfDocument(content)
    except Exception as exc:  # noqa: BLE001 — pdfium кидает свои исключения
        raise LimitsUnavailable("Описание типа — не PDF или файл повреждён") from exc

    try:
        texts = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range() or "")
            textpage.close()
            page.close()

        total = sum(len(t.strip()) for t in texts)
        if total >= TEXT_MIN_CHARS:
            relevant = [(i, t) for i, t in enumerate(texts) if KEYWORDS.search(t)] or list(enumerate(texts))
            chunks, size = [], 0
            for index, text in relevant:
                piece = f"--- страница {index + 1} ---\n{text.strip()}\n"
                if size + len(piece) > TEXT_MAX_CHARS:
                    break
                chunks.append(piece)
                size += len(piece)
            return "text", [{"type": "text", "text": "Текст описания типа:\n" + "\n".join(chunks)}]

        parts = []
        for index in range(min(len(pdf), MAX_PAGES_AS_IMAGES)):
            page = pdf[index]
            bitmap = page.render(scale=1.6)
            image = bitmap.to_pil().convert("RGB")
            bitmap.close()
            page.close()
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
            parts.append({"type": "image_bytes", "data": buffer.getvalue(), "mime": "image/jpeg"})
        if not parts:
            raise LimitsUnavailable("В описании типа нет страниц")
        return "images", parts
    finally:
        pdf.close()


def description_document(card: FifCard) -> FifDocument:
    registered = documents.register_documents(card)
    spec = [doc for doc in registered if doc.kind == FifDocument.Kind.DESCRIPTION]
    if not spec:
        raise LimitsUnavailable("В карточке типа нет описания типа")
    doc = documents.download(spec[0])
    if not doc.file:
        raise LimitsUnavailable(doc.error or "Описание типа не скачалось")
    return doc


def extract_limits(card: FifCard, variant: str, *, client: YandexVisionClient | None = None) -> dict:
    client = client or YandexVisionClient()
    if not client.is_configured:
        raise LimitsUnavailable("Чтение описаний типа не настроено (нет ключа Yandex AI Studio)")

    doc = description_document(card)
    with doc.file.open("rb") as handle:
        content = handle.read()
    mode, parts = _pdf_parts(content)

    prompt = PROMPT.format(variant=variant or card.notation or card.title)
    try:
        data = client.complete_json([{"type": "text", "text": prompt}, *parts], SCHEMA, name="type_limits", timeout=90)
    except RecognitionUnavailable as exc:
        raise LimitsUnavailable(str(exc)) from exc

    limits = {}
    for key in LIMIT_KEYS:
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            limits[key] = float(value)
    return {
        "found": bool(data.get("found")) and bool(limits),
        "limits": limits,
        "missing": [key for key in LIMIT_KEYS if key not in limits],
        "dn": data.get("dn"),
        "variant_matched": data.get("variant_matched"),
        "quote": (data.get("quote") or "")[:1500],
        "comment": (data.get("comment") or "")[:1000],
        "mode": mode,
        "document_id": doc.pk,
    }
