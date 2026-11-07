"""Госзакупки: поиск закупок на поверку в ЕИС (zakupki.gov.ru) по RSS.

У ЕИС нет открытого JSON API для поиска; есть RSS-выдача расширенного
поиска — тот же поиск, что на сайте, в формате RSS 2.0. Разбор устойчив к
мелким отличиям: номер берётся из ссылки или заголовка, цена и сроки — из
HTML-описания по подписям. Перед первым использованием на сервере:

    python manage.py tenders_probe "поверка счетчиков"

покажет, что пришло и что разобралось (как fif_probe для реестра ФИФ).
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import requests
from django.conf import settings
from django.utils import timezone

from apps.hub.models import Tender, TenderSearch

log = logging.getLogger(__name__)

RSS_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html"
REG_RE = re.compile(r"(?:regNumber=|№\s*)(\d{11,25})")
PRICE_RE = re.compile(r"(?:Начальная|Максимальная)[^:<]*цена[^:<]*:?\s*(?:</[^>]+>\s*)*([\d\s ]+(?:[.,]\d{1,2})?)", re.I)
CUSTOMER_RE = re.compile(r"(?:Заказчик|Организация, осуществляющая[^:]*|Размещено)\s*:\s*(?:</[^>]+>\s*)*([^<]+)", re.I)
DEADLINE_RE = re.compile(r"(?:Окончание подачи заявок|Дата окончания[^:]*)\s*:\s*(?:</[^>]+>\s*)*(\d{2}\.\d{2}\.\d{4})", re.I)
LAW_RE = re.compile(r"(44|223)-ФЗ")


class TendersUnavailable(RuntimeError):
    pass


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_rss(payload: bytes | str) -> list[dict]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise TendersUnavailable(f"ЕИС вернула не RSS: {exc}") from exc
    rows = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = item.findtext("description") or ""
        match = REG_RE.search(link) or REG_RE.search(title) or REG_RE.search(description)
        if not match:
            continue
        price = None
        if found := PRICE_RE.search(description):
            raw = re.sub(r"[\s ]", "", found.group(1)).replace(",", ".")
            try:
                price = Decimal(raw)
            except InvalidOperation:
                price = None
        published = None
        if item.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(item.findtext("pubDate"))
            except (TypeError, ValueError):
                published = None
        deadline = None
        if found := DEADLINE_RE.search(description):
            deadline = dt.datetime.strptime(found.group(1), "%d.%m.%Y").date()
        customer = _clean(found.group(1)) if (found := CUSTOMER_RE.search(description)) else ""
        law = found.group(1) if (found := LAW_RE.search(title + description)) else ""
        subject = _clean(description)
        obj = re.search(r"Наименование объекта закупки\s*:\s*(.+?)(?:\s{2,}|Заказчик|Начальная|Размещено|$)", subject)
        rows.append({
            "reg_number": match.group(1),
            "title": (obj.group(1).strip() if obj else _clean(title))[:2000],
            "url": link or f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={match.group(1)}",
            "customer": customer[:500],
            "law": law,
            "price": price,
            "published_at": published,
            "deadline": deadline,
        })
    return rows


def fetch(search: TenderSearch, *, session=None) -> list[dict]:
    params = {"searchString": search.query, "morphology": "on", "sortBy": "PUBLISH_DATE",
              "sortDirection": "false", "recordsPerPage": "_50", "af": "on"}
    for law in (search.laws or "").replace(" ", "").split(","):
        if law in {"44", "223"}:
            params[f"fz{law}"] = "on"
    if search.region:
        params["customerPlace"] = search.region
    proxy = getattr(settings, "TENDERS_PROXY", "")
    try:
        response = (session or requests).get(
            RSS_URL, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0 EI_doc"},
            proxies={"https": proxy, "http": proxy} if proxy else None,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TendersUnavailable(f"ЕИС недоступна: {exc}") from exc
    return parse_rss(response.content)


def run_search(search: TenderSearch, *, session=None) -> list[Tender]:
    """Выполнить поиск, сохранить новые закупки. Возвращает только новые."""
    try:
        rows = fetch(search, session=session)
    except TendersUnavailable as exc:
        search.last_error = str(exc)[:250]
        search.last_run_at = timezone.now()
        search.save(update_fields=["last_error", "last_run_at"])
        raise
    created = []
    for row in rows:
        tender, is_new = Tender.objects.get_or_create(reg_number=row.pop("reg_number"),
                                                      defaults={**row, "search": search})
        if is_new:
            created.append(tender)
    search.last_error = ""
    search.last_run_at = timezone.now()
    search.save(update_fields=["last_error", "last_run_at"])
    return created


def run_all(*, session=None) -> int:
    """Все активные поиски; о новых — в Telegram руководителю и менеджерам."""
    new: list[Tender] = []
    for search in TenderSearch.objects.filter(is_active=True):
        try:
            new += run_search(search, session=session)
        except TendersUnavailable as exc:
            log.warning("Поиск закупок «%s» не выполнен: %s", search.query, exc)
    if new:
        notify(new)
    return len(new)


def notify(tenders: list[Tender]) -> None:
    from apps.core import telegram
    from apps.core.models import Employee, Role

    lines = [f"Новые закупки на поверку: {len(tenders)}"]
    for t in tenders[:10]:
        price = f" · {t.price:,.0f} ₽".replace(",", " ") if t.price else ""
        until = f" · до {t.deadline:%d.%m}" if t.deadline else ""
        lines.append(f"• {t.title[:120]}{price}{until}\n{t.url}")
    text = "\n".join(lines)
    for employee in Employee.objects.filter(
        user__role__in=[Role.OWNER, Role.HEAD, Role.MANAGER], is_active=True
    ).exclude(telegram_chat_id=""):
        telegram.send_message(employee.telegram_chat_id, text)
