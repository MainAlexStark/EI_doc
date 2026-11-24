"""Карточка «Записи домена» — как mailDNS в openseller, но под почту Timeweb.

Ничего не меняем — только показываем, что есть и что должно быть, чтобы
письма с ящиков не падали в спам. Резолвер — DNS-over-HTTPS (dns.google):
в контейнере нет dnspython, а системный резолвер не умеет TXT. Кеш 10 минут.
DKIM-селектор Timeweb не публикует — берём его из подписи DKIM-Signature
наших же писем в «Отправленных», иначе пробуем частые (default, mail, dkim).
"""

from __future__ import annotations

import logging
import re

import requests
from django.core.cache import cache

from apps.mailbox import conf
from apps.mailbox.models import Direction, Message

logger = logging.getLogger("apps.mailbox")

TIMEWEB_MX = ("mx1.timeweb.ru", "mx2.timeweb.ru")
SPF_INCLUDE = "_spf.timeweb.ru"
DEFAULT_SELECTORS = ("default", "mail", "dkim", "timeweb")


def resolve(name: str, rtype: str) -> list[str]:
    try:
        r = requests.get("https://dns.google/resolve", params={"name": name, "type": rtype}, timeout=4)
        r.raise_for_status()
        return [a.get("data", "").strip('"').replace('" "', "") for a in r.json().get("Answer", [])
                if a.get("type") == {"MX": 15, "TXT": 16, "A": 1}[rtype]]
    except Exception as exc:  # noqa: BLE001 — нет сети: карточка честно скажет «не удалось проверить»
        logger.info("DNS %s %s: %s", rtype, name, exc)
        raise


def _selector(domain: str) -> str | None:
    from apps.mailbox.parse import message_from_bytes
    from apps.mailbox.store import read_raw

    for msg in Message.objects.filter(direction=Direction.OUT, uid__isnull=False).order_by("-received_at")[:10]:
        try:
            raw = read_raw(msg)
        except OSError:
            continue
        sig = str(message_from_bytes(raw[:20000]).get("DKIM-Signature", ""))
        tags = dict(re.findall(r"\b([ds])=([^;\s]+)", sig))
        if tags.get("s") and tags.get("d", "").lower() == domain:
            return tags["s"]
    return None


def check(domain: str) -> dict:
    key = f"mailbox:dns:{domain}"
    if (hit := cache.get(key)) is not None:
        return hit
    try:
        records = _records(domain)
        out = {"domain": domain, "records": records, "ok": all(r["ok"] for r in records), "error": ""}
    except Exception:  # noqa: BLE001
        out = {"domain": domain, "records": [], "ok": False, "error": "Не удалось проверить DNS — нет связи с dns.google"}
    cache.set(key, out, 600 if not out["error"] else 60)
    return out


def _records(domain: str) -> list[dict]:
    recs = []
    mx = resolve(domain, "MX")
    hosts = [h.split()[-1].rstrip(".").lower() for h in mx]
    recs.append({
        "type": "MX", "name": domain, "want": "10 mx1.timeweb.ru, 20 mx2.timeweb.ru", "have": ", ".join(mx),
        "ok": any(h in TIMEWEB_MX for h in hosts),
        "note": "куда доставляется почта домена — в ящики Timeweb (их и читает кабинет)",
    })
    txt = resolve(domain, "TXT")
    spf = next((t for t in txt if t.lower().startswith("v=spf1")), "")
    recs.append({
        "type": "TXT", "name": domain, "want": f"v=spf1 include:{SPF_INCLUDE} ~all", "have": spf,
        "ok": SPF_INCLUDE in spf.lower(), "note": "SPF: Timeweb вправе отправлять письма от имени домена",
    })
    selector = _selector(domain)
    found = ""
    for s in ([selector] if selector else []) + [s for s in DEFAULT_SELECTORS if s != selector]:
        got = resolve(f"{s}._domainkey.{domain}", "TXT")
        if got:
            selector, found = s, "".join(got)[:60] + "…"
            break
    recs.append({
        "type": "TXT", "name": f"{selector or '<селектор>'}._domainkey.{domain}",
        "want": "ключ DKIM из панели Timeweb → Почта → домен", "have": found, "ok": bool(found),
        "note": "DKIM: подпись писем; без неё Яндекс и Gmail чаще кладут в спам. "
                "Timeweb включает её сам после первого письма с ящика; селектор видно в DKIM-Signature",
    })
    dmarc = next((t for t in resolve(f"_dmarc.{domain}", "TXT") if t.lower().startswith("v=dmarc1")), "")
    recs.append({
        "type": "TXT", "name": f"_dmarc.{domain}", "want": "v=DMARC1; p=none", "have": dmarc, "ok": bool(dmarc),
        "note": "DMARC: что делать с поддельными письмами от имени домена",
    })
    return recs


def domains() -> list[str]:
    return sorted({b.domain for b in conf.boxes()})
