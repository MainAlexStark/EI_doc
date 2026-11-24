"""Почта фирмы в кабинете: ящики Timeweb по IMAP/SMTP (как «Почта» в openseller).

Своего приёмника нет: MX домена остаётся на Timeweb, письма забираются по IMAP
(apps.mailbox.sync), ответы уходят через SMTP того же ящика из общей очереди
писем (apps.notify.outbox) и кладутся копией в «Отправленные» (IMAP APPEND).
См. claude/mailbox.md в проекте.
"""
