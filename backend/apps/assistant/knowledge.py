"""Контекст ассистента: индексация файлов компании и поиск по ним.

Поиск — полнотекстовый PostgreSQL (словарь russian: «манометров» найдёт
«манометр»), без векторной БД: файлов у конторы сотни, не миллионы, а
полнотекстовый поиск хорошо работает с номерами («МИ 1997-89», «16078-05»),
на которых эмбеддинги как раз плывут. На SQLite (тесты) — простой поиск по
вхождению слов с подсчётом совпадений.
"""

from __future__ import annotations

import re

from django.db import connection, transaction
from django.db.models import Q

from apps.assistant.extract import ExtractError, chunk_text, extract_text, guess_mime
from apps.assistant.models import ContextMode, KnowledgeChunk, KnowledgeFile, Scope

WORD_RE = re.compile(r"[\wА-Яа-яЁё][\w\-./А-Яа-яЁё]*", re.UNICODE)


def visible_files(user):
    """Файлы, которые агент учитывает для этого человека: общие + его личные."""
    return KnowledgeFile.objects.filter(
        Q(scope=Scope.COMPANY) | Q(scope=Scope.PERSONAL, owner=user)
    )


def index_file(kf: KnowledgeFile, data: bytes | None = None) -> KnowledgeFile:
    """Извлечь текст и пересобрать куски. data — содержимое, если файл ещё не сохранён."""
    if data is None and kf.file:
        kf.file.open("rb")
        try:
            data = kf.file.read()
        finally:
            kf.file.close()
    if data is not None:
        name = kf.original_name or (kf.file.name if kf.file else "file.txt")
        kf.mime = guess_mime(name, kf.mime)
        try:
            kf.text = extract_text(data, name, kf.mime)
            kf.extract_error = ""
        except ExtractError as exc:
            kf.text = ""
            kf.extract_error = str(exc)[:500]
    kf.chars = len(kf.text)
    with transaction.atomic():
        kf.save()
        kf.chunks.all().delete()
        KnowledgeChunk.objects.bulk_create(
            KnowledgeChunk(file=kf, index=i, heading=heading, text=body)
            for i, (heading, body) in enumerate(chunk_text(kf.text))
        )
    return kf


def _terms(query: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(query or "") if len(t) > 1][:12]


def search(user, query: str, *, category: str = "", file_ids: list[int] | None = None, limit: int = 8):
    """Лучшие куски по запросу. Возвращает [(chunk, score)]."""
    terms = _terms(query)
    if not terms:
        return []
    files = visible_files(user).exclude(mode=ContextMode.OFF)
    if category:
        files = files.filter(category=category)
    if file_ids:
        files = files.filter(pk__in=file_ids)
    qs = KnowledgeChunk.objects.filter(file__in=files).select_related("file")

    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

        vector = SearchVector("heading", weight="A", config="russian") + SearchVector(
            "text", weight="B", config="russian"
        )
        # OR между словами: запрос человека часто содержит лишние слова.
        ts_query = None
        for term in terms:
            part = SearchQuery(term, config="russian", search_type="plain")
            ts_query = part if ts_query is None else ts_query | part
        ranked = (
            qs.annotate(rank=SearchRank(vector, ts_query))
            .filter(rank__gt=0)
            .order_by("-rank")[: limit * 3]
        )
        results = [(chunk, float(chunk.rank)) for chunk in ranked]
        # Точные вхождения номеров («16078-05», «МИ 1592») поднимаем выше.
        exact = [t for t in terms if any(ch.isdigit() for ch in t)]
        if exact:
            for i, (chunk, score) in enumerate(results):
                body = chunk.text.lower()
                results[i] = (chunk, score + sum(1.0 for t in exact if t in body))
            if not results:
                cond = Q()
                for t in exact:
                    cond |= Q(text__icontains=t)
                results = [(c, 1.0) for c in qs.filter(cond)[:limit]]
        results.sort(key=lambda item: -item[1])
        return results[:limit]

    # SQLite (тесты, локальный запуск): LIKE не сравнивает кириллицу без учёта
    # регистра, поэтому считаем совпадения в Python — корпус там маленький.
    scored = []
    for chunk in qs[:5000]:
        body = (chunk.heading + " " + chunk.text).lower()
        hits = sum(1 for t in terms if t in body)
        if hits:
            scored.append((chunk, float(sum(body.count(t) for t in terms) + 3 * hits)))
    scored.sort(key=lambda item: -item[1])
    return scored[:limit]
