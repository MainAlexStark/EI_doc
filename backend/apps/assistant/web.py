"""Интернет для ассистента: поиск (Yandex Search API) и чтение страниц/PDF.

Поиск — Yandex Search API v2 (тот же каталог Yandex Cloud, что и распознавание
бланков; сервисному аккаунту нужна роль ``search-api.webSearch.user``).
Чтение страницы отдаёт текст, а не HTML: описания типов СИ и методики на
fgis.gost.ru — это PDF, они разбираются тем же извлекателем, что и файлы
контекста.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache

from apps.assistant.extract import ExtractError, extract_text, html_to_text

SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) EI_doc-assistant/1.0"
MAX_DOWNLOAD = 25 * 1024 * 1024


class WebError(RuntimeError):
    pass


def search_configured() -> bool:
    return bool(_search_key() and settings.YANDEX_VISION_FOLDER_ID)


def _search_key() -> str:
    return settings.YANDEX_SEARCH_API_KEY or settings.YANDEX_VISION_API_KEY


def search(query: str, *, page: int = 0, region: str = "225") -> list[dict]:
    if not search_configured():
        raise WebError(
            "Веб-поиск выключен: не заданы YANDEX_SEARCH_API_KEY (или YANDEX_VISION_API_KEY) "
            "и YANDEX_VISION_FOLDER_ID"
        )
    key = f"assistant:web:{query.lower()}:{page}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    body = {
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query, "page": str(page)},
        "groupSpec": {"groupMode": "GROUP_MODE_DEEP", "groupsOnPage": "10", "docsInGroup": "1"},
        "maxPassages": "3",
        "region": region,
        "l10N": "LOCALIZATION_RU",
        "folderId": settings.YANDEX_VISION_FOLDER_ID,
        "responseFormat": "FORMAT_XML",
    }
    try:
        response = requests.post(
            SEARCH_URL, json=body, timeout=30,
            headers={"Authorization": f"Api-Key {_search_key()}"},
        )
    except requests.RequestException as exc:
        raise WebError(f"Поиск недоступен: {exc}") from exc
    if response.status_code >= 400:
        raise WebError(f"Yandex Search ответил {response.status_code}: {response.text[:300]}")
    try:
        raw = base64.b64decode(response.json()["rawData"])
    except (ValueError, KeyError) as exc:
        raise WebError("Yandex Search вернул неожиданный ответ") from exc
    results = parse_search_xml(raw)
    cache.set(key, results, 3600)
    return results


def _text(node) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def parse_search_xml(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    error = root.find(".//response/error")
    if error is not None:
        raise WebError(f"Yandex Search: {_text(error)}")
    results = []
    for doc in root.iter("doc"):
        passages = [_text(p) for p in doc.iter("passage")]
        headline = _text(doc.find("headline"))
        results.append({
            "url": _text(doc.find("url")),
            "title": _text(doc.find("title")),
            "snippet": " … ".join(p for p in passages if p) or headline,
            "date": _text(doc.find("modtime")),
        })
    return results


# ---------------------------------------------------------------------------
# Чтение страницы
# ---------------------------------------------------------------------------
def _check_public(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebError("Можно открывать только http(s)-адреса")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise WebError(f"Адрес не найден: {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise WebError("Внутренние адреса сервера открывать нельзя")


def fetch(url: str) -> tuple[str, str]:
    """Скачать и вернуть (текст, итоговый адрес). Кэш на сутки."""
    key = f"assistant:fetch:{url}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    _check_public(url)
    try:
        response = requests.get(
            url, timeout=(10, 40), stream=True, allow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.5"},
        )
    except requests.RequestException as exc:
        raise WebError(f"Страница не открылась: {exc}") from exc
    if response.status_code >= 400:
        raise WebError(f"Сайт ответил {response.status_code}")
    _check_public(response.url)
    data = b""
    for part in response.iter_content(65536):
        data += part
        if len(data) > MAX_DOWNLOAD:
            raise WebError("Файл больше 25 МБ — не скачиваю")
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    name = urlparse(response.url).path.rsplit("/", 1)[-1] or "page.html"

    if content_type in ("text/html", "application/xhtml+xml") or (
        not content_type and data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))
    ):
        encoding = response.encoding if response.encoding and response.encoding.lower() != "iso-8859-1" else None
        try:
            html = data.decode(encoding or response.apparent_encoding or "utf-8", errors="replace")
        except LookupError:
            html = data.decode("utf-8", errors="replace")
        text = html_to_text(html)
    else:
        if content_type == "application/pdf" and not name.lower().endswith(".pdf"):
            name += ".pdf"
        try:
            text = extract_text(data, name, content_type)
        except ExtractError as exc:
            raise WebError(str(exc)) from exc
    result = (text, response.url)
    cache.set(key, result, 24 * 3600)
    return result
