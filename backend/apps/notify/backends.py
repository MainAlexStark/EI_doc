"""Почтовый бэкенд «в журнал» — когда SMTP_HOST не задан.

Как Log в openseller: письма никуда не уходят, а пишутся в лог (кому и тема,
тело — на уровне DEBUG). Так кабинет работает и без настроенной почты:
сброс пароля честно отвечает «почта не настроена», уведомления копятся
в очереди со статусом «отправлено в журнал».
"""

from __future__ import annotations

import logging

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger("apps.notify.mail")


class LogBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        for message in email_messages or []:
            logger.info(
                "Почта не настроена (SMTP_HOST пуст) — письмо «%s» для %s никуда не ушло",
                message.subject, ", ".join(message.to),
            )
            logger.debug("%s", message.body)
        return len(email_messages or [])
