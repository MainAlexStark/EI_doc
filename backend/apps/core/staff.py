"""Сотрудники и их учётные записи — одним действием, без Django-админки.

Раньше, чтобы добавить работника, надо было в /admin/ завести пользователя,
потом сотрудника, потом связать их. Здесь это одна операция: ФИО, должность,
роль, почта — и выдаётся временный пароль, который показывается один раз.

Кто что может:
* видеть список — всё руководство (``dispatch.CONTROL_ROLES``);
* заводить, менять, увольнять, сбрасывать пароль — владелец и руководитель;
* сделать кого-то владельцем или поменять владельца — только владелец;
* себя не уволить и не лишить своей роли (иначе можно остаться без администратора).
"""

from __future__ import annotations

import re
import secrets
import string

from django.db import transaction
from django.db.models import Max, Min, Q
from django.utils import timezone

from apps.core.models import Employee, Role, User

ADMIN_ROLES = {Role.OWNER, Role.HEAD}
_ALPHABET = "".join(ch for ch in string.ascii_letters + string.digits if ch not in "0OoIl1")


class StaffError(ValueError):
    """Операция невозможна — текст показывается человеку как есть."""


def can_admin(user) -> bool:
    return bool(getattr(user, "is_superuser", False) or getattr(user, "role", "") in ADMIN_ROLES)


def temp_password() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def next_tab_number() -> str:
    numbers = [int(n) for n in Employee.objects.values_list("tab_number", flat=True) if str(n).isdigit()]
    return f"{(max(numbers) + 1 if numbers else 1):02d}"


def short_name(full_name: str) -> str:
    last, *rest = full_name.split() or [""]
    return " ".join([last, *(f"{part[0]}." for part in rest if part)])


def row(e: Employee) -> dict:
    user = e.user
    today = timezone.localdate()
    att = e.attestations.aggregate(valid_to=Max("valid_to", filter=Q(valid_from__lte=today)))
    nearest = e.attestations.filter(valid_to__gte=today).aggregate(v=Min("valid_to"))["v"]
    return {
        "id": e.pk,
        "full_name": e.full_name,
        "short_name": e.short_name or short_name(e.full_name),
        "tab_number": e.tab_number,
        "position": e.position,
        "is_active": e.is_active,
        "email": user.email if user else "",
        "role": user.role if user else "",
        "role_display": user.get_role_display() if user else "Без входа",
        "has_login": bool(user and user.is_active and user.has_usable_password()),
        "last_login": user.last_login if user else None,
        "telegram_linked": bool(e.telegram_chat_id),
        "attestation_valid_to": att["valid_to"],
        "attestation_nearest": nearest,
        "attestation_ok": bool(att["valid_to"] and att["valid_to"] >= today),
    }


def roles() -> list[dict]:
    return [{"value": value, "label": label} for value, label in Role.choices]


def _check_role(actor, role: str) -> None:
    if role not in Role.values:
        raise StaffError("Неизвестная роль")
    if role == Role.OWNER and not (actor.is_superuser or actor.role == Role.OWNER):
        raise StaffError("Сделать владельцем может только владелец")


def _check_email(email: str, exclude_user_id: int | None = None) -> str:
    email = (email or "").strip().lower()
    if not email:
        return ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise StaffError(f"Почта {email} выглядит неправильно")
    if User.objects.filter(email__iexact=email).exclude(pk=exclude_user_id).exists():
        raise StaffError(f"Почта {email} уже занята другой учётной записью")
    return email


@transaction.atomic
def create(actor, *, full_name: str, position: str = "", email: str = "", role: str = Role.VERIFIER,
           tab_number: str = "", short: str = "", password: str = "") -> tuple[Employee, str]:
    """Новый сотрудник (+ учётная запись, если дали почту). Возвращает временный пароль ('' — без входа)."""
    if not can_admin(actor):
        raise StaffError("Сотрудников заводят владелец и руководитель")
    full_name = " ".join((full_name or "").split())
    if len(full_name) < 3:
        raise StaffError("Укажите ФИО")
    tab_number = (tab_number or "").strip() or next_tab_number()
    if Employee.objects.filter(tab_number=tab_number).exists():
        raise StaffError(f"Табельный номер {tab_number} уже занят")
    email = _check_email(email)
    _check_role(actor, role)
    user = None
    secret = ""
    if email:
        secret = password or temp_password()
        user = User.objects.create_user(email=email, password=secret, role=role)
    employee = Employee.objects.create(
        user=user, full_name=full_name, short_name=(short or short_name(full_name))[:80],
        position=(position or "").strip()[:120], tab_number=tab_number,
    )
    return employee, secret


@transaction.atomic
def update(actor, employee: Employee, **changes) -> tuple[Employee, str]:
    """Поменять карточку/роль/почту, уволить (is_active=False), вернуть. Пароль — только если завели вход."""
    if not can_admin(actor):
        raise StaffError("Сотрудников меняют владелец и руководитель")
    user = employee.user
    is_self = user is not None and user.pk == actor.pk
    secret = ""

    if "full_name" in changes and changes["full_name"] is not None:
        name = " ".join(str(changes["full_name"]).split())
        if len(name) < 3:
            raise StaffError("Укажите ФИО")
        employee.full_name = name
        if not changes.get("short_name"):
            employee.short_name = short_name(name)[:80]
    if changes.get("short_name"):
        employee.short_name = str(changes["short_name"]).strip()[:80]
    if changes.get("position") is not None:
        employee.position = str(changes["position"]).strip()[:120]
    if changes.get("tab_number"):
        tab = str(changes["tab_number"]).strip()
        if Employee.objects.filter(tab_number=tab).exclude(pk=employee.pk).exists():
            raise StaffError(f"Табельный номер {tab} уже занят")
        employee.tab_number = tab

    if "is_active" in changes and changes["is_active"] is not None:
        active = bool(changes["is_active"])
        if is_self and not active:
            raise StaffError("Себя уволить нельзя")
        if not active and user and user.role == Role.OWNER and not (actor.is_superuser or actor.role == Role.OWNER):
            raise StaffError("Владельца увольняет только владелец")
        employee.is_active = active
        if user:
            user.is_active = active
            user.save(update_fields=["is_active"])

    role = changes.get("role")
    email = changes.get("email")
    if email is not None and str(email).strip():
        email = _check_email(str(email), user.pk if user else None)
        if user is None:
            secret = temp_password()
            user = User.objects.create_user(email=email, password=secret, role=role or Role.VERIFIER)
            employee.user = user
        elif user.email != email:
            user.email = email
            user.save(update_fields=["email"])
    if role:
        if user is None:
            raise StaffError("Роль даётся вместе с учётной записью — укажите почту для входа")
        _check_role(actor, role)
        if is_self and role != user.role:
            raise StaffError("Свою роль поменять нельзя — попросите другого руководителя")
        if user.role == Role.OWNER and role != Role.OWNER and not (actor.is_superuser or actor.role == Role.OWNER):
            raise StaffError("Роль владельца меняет только владелец")
        if user.role != role:
            user.role = role
            user.save(update_fields=["role"])
    employee.save()
    return employee, secret


def reset_password(actor, employee: Employee) -> str:
    if not can_admin(actor):
        raise StaffError("Пароль сбрасывают владелец и руководитель")
    user = employee.user
    if user is None:
        raise StaffError("У сотрудника нет учётной записи — сначала укажите почту для входа")
    if user.role == Role.OWNER and user.pk != actor.pk and not (actor.is_superuser or actor.role == Role.OWNER):
        raise StaffError("Пароль владельца сбрасывает только владелец")
    secret = temp_password()
    user.set_password(secret)
    user.save(update_fields=["password"])
    return secret

