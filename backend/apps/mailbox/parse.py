"""Разбор письма: кто, кому, тема, текст, очищенный HTML, вложения.

Как internal/inbox/parse.go в openseller. Кодировки (koi8-r, windows-1251…)
переводятся в UTF-8; нераспознанная — как windows-1251 с заменой битых байт
(русская почта чаще всего именно она). HTML письма — чужой код: в базу кладём
только очищенный nh3 (без скриптов, обработчиков, форм, javascript:), а
показываем ещё и в песочнице с CSP (api.MessageBodyView).
"""

from __future__ import annotations

import base64
import datetime as dt
import html as htmllib
import re
from dataclasses import dataclass, field
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

import nh3

ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "code", "col", "colgroup", "dd", "del", "div",
    "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "li", "ol", "p",
    "pre", "q", "s", "small", "span", "strike", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "tt", "u", "ul",
}
_COMMON = {"style", "class", "align", "dir", "title", "lang"}
ALLOWED_ATTRS = {
    "*": _COMMON,
    "a": {"href", "name", "target"},
    "img": {"src", "alt", "width", "height", "border"},
    "font": {"color", "face", "size"},
    "table": {"width", "border", "cellpadding", "cellspacing", "bgcolor"},
    "td": {"width", "height", "colspan", "rowspan", "valign", "bgcolor", "nowrap"},
    "th": {"width", "height", "colspan", "rowspan", "valign", "bgcolor", "nowrap"},
    "tr": {"valign", "bgcolor"},
    "col": {"width", "span"},
    "ol": {"start", "type"},
}
# Внешняя картинка: src=/url() на http(s):// или протокол-относительный //host.
_REMOTE_RE = re.compile(r"""(?:\bsrc\s*=\s*["']?\s*(?:https?:)?//|url\(\s*(?:&quot;|["'])?\s*(?:https?:)?//)""", re.I)


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    v = value.strip().lower()
    if attr == "href" and not v.startswith(("http://", "https://", "mailto:", "#")):
        return None  # data:, javascript:, vbscript: и прочее — без ссылки
    if attr == "src" and tag == "img" and not (
        v.startswith(("http://", "https://", "//", "cid:")) or v.startswith(tuple(f"data:{t};" for t in INLINE_TYPES))
    ):
        return None
    if attr == "src" and tag != "img":
        return None
    if attr == "style" and ("expression" in v or "javascript:" in v or "behavior" in v or "@import" in v):
        return None
    if attr == "target":
        return "_blank"
    return value


def sanitize(raw_html: str) -> str:
    if not raw_html:
        return ""
    return nh3.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto", "cid", "data"},
        attribute_filter=_attribute_filter,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


def has_remote_images(clean_html: str) -> bool:
    return bool(_REMOTE_RE.search(clean_html or ""))


_TAG_RE = re.compile(r"<[^>]+>")
_DROP_RE = re.compile(r"<(script|style|head|title)\b.*?</\1\s*>", re.I | re.S)
_BLOCK_RE = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h\d|/table|hr)\b[^>]*>", re.I)


def html_to_text(value: str) -> str:
    """Грубый текст из HTML — для поиска, превью и ассистента."""
    if not value:
        return ""
    value = _DROP_RE.sub(" ", value)
    value = _BLOCK_RE.sub("\n", value)
    value = htmllib.unescape(_TAG_RE.sub("", value))
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def decode_header_value(value) -> str:
    if value is None:
        return ""
    value = str(value)
    try:
        value = str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — кривой заголовок: как есть
        pass
    return " ".join(value.split())


@dataclass
class Attachment:
    n: int
    name: str
    type: str
    size: int
    cid: str = ""
    inline: bool = False

    def as_dict(self) -> dict:
        return {"n": self.n, "name": self.name, "type": self.type, "size": self.size, "cid": self.cid,
                "inline": self.inline}


@dataclass
class Parsed:
    message_id: str = ""
    in_reply_to: str = ""
    refs: str = ""
    from_addr: str = ""
    from_name: str = ""
    to: str = ""
    cc: str = ""
    reply_to: str = ""
    subject: str = ""
    date: dt.datetime | None = None
    text: str = ""
    html: str = ""
    remote_images: bool = False
    attachments: list[Attachment] = field(default_factory=list)
    auto_submitted: bool = False
    dkim: tuple[str, str] | None = None  # (d=, s=) из DKIM-Signature

    @property
    def preview(self) -> str:
        return " ".join((self.text or html_to_text(self.html)).split())[:280]


def _ids(value: str) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


def _addr_list(msg: Message, key: str) -> str:
    values = msg.get_all(key) or []
    pairs = getaddresses([decode_header_value(v) for v in values])
    out = []
    for name, addr in pairs:
        if not addr:
            continue
        out.append(f"{name} <{addr}>" if name else addr)
    return ", ".join(out)


def _leaves(msg: Message):
    """Листовые части по порядку — номер части стабилен для Part(n)."""
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_type() == "message/rfc822":
            continue
        yield part


def _payload_text(part: Message) -> str:
    data = part.get_payload(decode=True) or b""
    charset = (part.get_content_charset() or "").lower()
    for enc in [c for c in (charset, "utf-8", "windows-1251") if c]:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("windows-1251", errors="replace")


def _filename(part: Message) -> str:
    name = part.get_filename()
    if name:
        return decode_header_value(name).replace("/", "_").replace("\\", "_")[:200]
    return ""


def _default_name(n: int, ctype: str) -> str:
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "application/pdf": ".pdf",
           "text/plain": ".txt", "text/calendar": ".ics", "message/delivery-status": ".txt"}.get(ctype, ".bin")
    return f"часть-{n}{ext}"


def message_from_bytes(raw: bytes) -> EmailMessage:
    return BytesParser(policy=policy.compat32).parsebytes(raw)


def parse(raw: bytes) -> Parsed:
    msg = message_from_bytes(raw)
    p = Parsed()
    p.message_id = (_ids(msg.get("Message-ID", "")) or [""])[0][:500]
    p.in_reply_to = (_ids(msg.get("In-Reply-To", "")) or [""])[0][:500]
    p.refs = " ".join(_ids(" ".join(str(v) for v in (msg.get_all("References") or []))))
    name, addr = parseaddr(decode_header_value(msg.get("From", "")))
    p.from_name, p.from_addr = name[:300], addr.lower()[:320]
    p.to = _addr_list(msg, "To")
    p.cc = _addr_list(msg, "Cc")
    p.reply_to = parseaddr(decode_header_value(msg.get("Reply-To", "")))[1][:320]
    p.subject = decode_header_value(msg.get("Subject", ""))[:1000]
    try:
        p.date = parsedate_to_datetime(msg.get("Date", "")) if msg.get("Date") else None
        if p.date is not None and p.date.tzinfo is None:
            p.date = p.date.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError, IndexError):
        p.date = None
    auto = (msg.get("Auto-Submitted", "") or "").lower()
    p.auto_submitted = (auto not in ("", "no")) or bool(msg.get("X-EI-Doc-Notify"))
    sig = msg.get("DKIM-Signature", "")
    if sig:
        tags = dict(re.findall(r"\b([ds])=([^;\s]+)", str(sig)))
        if tags.get("s"):
            p.dkim = (tags.get("d", ""), tags["s"])

    text_parts: list[str] = []
    html_parts: list[str] = []
    for n, part in enumerate(_leaves(msg)):
        ctype = part.get_content_type()
        disp = (part.get("Content-Disposition") or "").split(";")[0].strip().lower()
        fname = _filename(part)
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        is_body = disp != "attachment" and not fname and ctype in ("text/plain", "text/html")
        if is_body:
            (html_parts if ctype == "text/html" else text_parts).append(_payload_text(part))
            continue
        data = part.get_payload(decode=True) or b""
        p.attachments.append(Attachment(
            n=n, name=fname or _default_name(n, ctype), type=ctype, size=len(data), cid=cid,
            inline=bool(cid) and ctype.startswith("image/") and disp != "attachment",
        ))
    p.text = "\n\n".join(t.strip() for t in text_parts if t.strip())
    p.html = sanitize("\n".join(html_parts)) if html_parts else ""
    if not p.text and p.html:
        p.text = html_to_text(p.html)
    p.remote_images = has_remote_images(p.html)
    return p


def part(raw: bytes, n: int) -> tuple[Attachment, bytes] | None:
    """n-я листовая часть: (описание, байты). Нет такой — None."""
    msg = message_from_bytes(raw)
    for i, leaf in enumerate(_leaves(msg)):
        if i == n:
            ctype = leaf.get_content_type()
            data = leaf.get_payload(decode=True) or b""
            return Attachment(n=i, name=_filename(leaf) or _default_name(i, ctype), type=ctype, size=len(data)), data
    return None


INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_CID_SRC_RE = re.compile(r"""(\bsrc\s*=\s*)(["'])cid:([^"'<>\s]+)\2""", re.I)


def inline_cids(raw: bytes, clean_html: str, limit: int = 5 << 20) -> str:
    """cid:… в src картинок → data:-адреса встроенных частей: песочнице запрещено ходить даже на наш сервер.

    Тип части — только из белого списка (Content-Type письма чужой: «image/x"><meta …»
    иначе вставил бы разметку), подставляем только внутрь src, а результат ещё раз
    чистим nh3 — что бы ни пришло, HTML остаётся очищенным."""
    if "cid:" not in (clean_html or "").lower():
        return clean_html
    msg = message_from_bytes(raw)
    images: dict[str, str] = {}
    total = 0
    for leaf in _leaves(msg):
        cid = (leaf.get("Content-ID") or "").strip().strip("<>").lower()
        ctype = leaf.get_content_type().lower()
        if not cid or ctype not in INLINE_TYPES:
            continue
        data = leaf.get_payload(decode=True) or b""
        total += len(data)
        if total > limit:
            break
        images[cid] = f"data:{ctype};base64,{base64.b64encode(data).decode('ascii')}"

    def repl(m: re.Match) -> str:
        uri = images.get(htmllib.unescape(m.group(3)).lower())
        if uri is None:
            return m.group(0)
        return f'{m.group(1)}"{htmllib.escape(uri, quote=True)}"'

    return sanitize(_CID_SRC_RE.sub(repl, clean_html))


def safe_attachment_type(ctype: str) -> str:
    """Как safeAttachmentType в openseller: известные безобидные типы — как есть, остальное — octet-stream."""
    ctype = (ctype or "").lower()
    if (ctype.startswith("image/") and ctype != "image/svg+xml") or ctype in (
        "application/pdf", "application/zip", "text/plain", "text/csv",
    ) or ctype.startswith("application/vnd."):
        return ctype
    return "application/octet-stream"
