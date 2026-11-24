"""Письма по шаблонам: тема, простой текст (обязателен) и HTML в общем каркасе.

Как templates.go в openseller: у каждого письма notify/<имя>.txt и
notify/<имя>.html (каркас — notify/layout.html), тема — в SUBJECTS или в
контексте («subject»), если она зависит от содержимого (сводки).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.template.loader import render_to_string

BRAND = "EI_doc"

SUBJECTS = {
    "password_reset": f"Сброс пароля — {BRAND}",
    "password_changed": f"Пароль изменён — {BRAND}",
    "password_reset_by_admin": f"Руководитель сбросил ваш пароль — {BRAND}",
    "digest": "",  # тема в контексте
    "test": f"Проверка почты — {BRAND}",
}


@dataclass
class Rendered:
    subject: str
    text: str
    html: str


def absolute(path: str = "/") -> str:
    base = getattr(settings, "APP_PUBLIC_URL", "") or ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{base}{path if path.startswith('/') else '/' + path}"


def render(name: str, context: dict) -> Rendered:
    if name not in SUBJECTS:
        raise KeyError(f"Нет шаблона письма {name!r}")
    ctx = {"brand": BRAND, "app_url": absolute("/"), **context}
    subject = ctx.get("subject") or SUBJECTS[name]
    text = render_to_string(f"notify/{name}.txt", ctx).strip() + "\n"
    html = render_to_string(f"notify/{name}.html", ctx)
    return Rendered(subject=subject, text=text, html=html)
