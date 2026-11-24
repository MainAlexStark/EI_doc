"""Режим шифрования SMTP — без Django, чтобы им пользовались settings/base.py.

Та же логика, что в openseller (internal/mail.SMTP.implicitTLS):
SMTP_TLS = implicit | ssl | tls | smtps → TLS сразу при подключении (SMTPS);
starttls → обычное подключение и STARTTLS; пусто — по порту: 465 → implicit,
остальные (587, 25, 1126…) → STARTTLS. Открытым текстом не отправляем никогда:
если сервер не умеет STARTTLS, smtplib падает, а не шлёт пароль как есть.
"""

from __future__ import annotations

IMPLICIT_MODES = {"implicit", "ssl", "tls", "smtps"}


def implicit_tls(port: int, mode: str = "") -> bool:
    mode = (mode or "").strip().lower()
    if mode in IMPLICIT_MODES:
        return True
    if mode == "starttls":
        return False
    return int(port) == 465
