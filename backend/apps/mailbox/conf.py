"""Ящики из окружения — без БД, чтобы читать их и из settings, и из Celery.

MAILBOXES="info=info@eicounters.ru,zakupki=zakupki@eicounters.ru" — имя ящика
(латиница, для вкладок и переменных) и адрес. Пароль — MAILBOX_PASSWORD_<ИМЯ>
(MAILBOX_PASSWORD_INFO). Хосты по умолчанию — Timeweb: IMAP imap.timeweb.ru:993
(SSL), SMTP smtp.timeweb.ru:465 (SSL); у отдельного ящика можно переопределить
MAILBOX_IMAP_HOST_<ИМЯ>, MAILBOX_SMTP_HOST_<ИМЯ> и т.п. Пустой MAILBOXES —
панель «Почта» выключена и показывает, что вписать.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from email.utils import parseaddr

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


@dataclass(frozen=True)
class Box:
    name: str
    address: str
    password: str = field(repr=False)  # не светить пароль в логах и трейсах
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    smtp_tls: str
    from_name: str

    @property
    def domain(self) -> str:
        return self.address.rsplit("@", 1)[-1]

    @property
    def configured(self) -> bool:
        return bool(self.password)


def _env(key: str, name: str, default: str) -> str:
    return (os.environ.get(f"{key}_{name.upper()}") or os.environ.get(key) or default).strip()


def boxes() -> list[Box]:
    raw = os.environ.get("MAILBOXES", "")
    out: list[Box] = []
    seen: set[str] = set()
    for item in re.split(r"[,;\s]+", raw.strip()):
        if not item:
            continue
        name, _, address = item.partition("=")
        if not address:  # «info@eicounters.ru» без имени — имя из адреса
            address, name = name, name.split("@", 1)[0]
        name = name.strip().lower().replace(".", "-")
        address = parseaddr(address.strip())[1].lower()
        if not _NAME_RE.match(name) or "@" not in address or name in seen:
            continue
        seen.add(name)
        out.append(Box(
            name=name,
            address=address,
            password=os.environ.get(f"MAILBOX_PASSWORD_{name.upper().replace('-', '_')}", ""),
            imap_host=_env("MAILBOX_IMAP_HOST", name, "imap.timeweb.ru"),
            imap_port=int(_env("MAILBOX_IMAP_PORT", name, "993")),
            smtp_host=_env("MAILBOX_SMTP_HOST", name, "smtp.timeweb.ru"),
            smtp_port=int(_env("MAILBOX_SMTP_PORT", name, "465")),
            smtp_tls=_env("MAILBOX_SMTP_TLS", name, ""),
            from_name=_env("MAILBOX_FROM_NAME", name, "ООО «Единица Измерения»"),
        ))
    return out


def box(name: str) -> Box | None:
    return next((b for b in boxes() if b.name == name), None)


def enabled() -> bool:
    return any(b.configured for b in boxes())


def addresses() -> set[str]:
    """Адреса ящиков фирмы — на них не шлём уведомления о новой почте (петля)."""
    return {b.address for b in boxes()}


def max_bytes() -> int:
    return int(os.environ.get("MAILBOX_MAX_MB", "25")) << 20


def initial_limit() -> int:
    return int(os.environ.get("MAILBOX_INITIAL_LIMIT", "500"))


def sync_batch() -> int:
    """Сколько писем на ящик забрать за один запуск синхронизации (остальное — следующий тик)."""
    return int(os.environ.get("MAILBOX_SYNC_BATCH", "200"))
