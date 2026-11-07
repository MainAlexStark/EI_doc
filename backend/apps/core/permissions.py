"""Роли и права — одно место для всех правил «кто что может».

Шесть ролей из docs/architecture.md: владелец, руководитель, метролог /
нормоконтроль, менеджер, поверитель, наблюдатель. Правила описаны
предикатами django-rules и регистрируются как обычные права Django, поэтому
одинаково работают в API (`HasPerm`), в админке и в коде (`user.has_perm`).

Право подписи протокола здесь — только допуск по роли. Действующая аттестация
на семейство СИ проверяется доменным слоем (`Employee.can_sign`,
`numbering.seal`), а не тут: роль без аттестации подписать не даст.
"""

from __future__ import annotations

import rules
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core.models import Role


def _role_is(*roles: str):
    allowed = {str(r) for r in roles}

    @rules.predicate
    def check(user) -> bool:
        return bool(user and user.is_authenticated and getattr(user, "role", None) in allowed)

    check.__name__ = "role_in_" + "_".join(sorted(allowed))
    return check


@rules.predicate
def is_superuser(user) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)


is_owner = _role_is(Role.OWNER)
is_head = _role_is(Role.HEAD)
is_metrologist = _role_is(Role.METROLOGIST)
is_manager = _role_is(Role.MANAGER)
is_verifier = _role_is(Role.VERIFIER)
is_observer = _role_is(Role.OBSERVER)

is_leadership = is_superuser | is_owner | is_head
is_quality = is_leadership | is_metrologist          # нормоконтроль, справочники, Аршин
is_dispatch = is_leadership | is_manager             # заявки, наряды, командировки
is_staff_member = is_quality | is_manager | is_verifier  # все, кроме наблюдателя

# Право → предикат. Имена в стиле Django: <приложение>.<действие>.
PERMISSIONS = {
    # Журнал читают все; наблюдатель — только читает.
    "verification.view_journal": rules.is_authenticated,
    "verification.normocontrol": is_quality,
    "verification.sign_protocol": is_quality | is_verifier,
    "verification.field_work": is_staff_member,
    "verification.manage_workorders": is_dispatch,
    "hub.manage_requests": is_dispatch,
    "logistics.view_trips": is_staff_member,
    "logistics.plan_trips": is_dispatch,
    "arshin.submit": is_quality,
    "core.manage_contracts": is_dispatch,
    "core.manage_personal_data": is_leadership,
    "core.write": is_staff_member,
}

for _name, _predicate in PERMISSIONS.items():
    rules.set_perm(_name, _predicate)


def role_of(user) -> str:
    return getattr(user, "role", "") or ""


def user_permissions(user) -> list[str]:
    """Список прав пользователя — фронт прячет кнопки, которые всё равно откажут."""
    return sorted(name for name in PERMISSIONS if user.has_perm(name))


class HasPerm(BasePermission):
    """DRF-разрешение по правилу: `permission_classes = [HasPerm.of("…")]`.

    `safe_perm` — отдельное право для GET/HEAD/OPTIONS, если читать можно
    шире, чем менять.
    """

    perm: str = ""
    safe_perm: str | None = None
    message = "Недостаточно прав для этого действия"

    @classmethod
    def of(cls, perm: str, safe_perm: str | None = None) -> type[HasPerm]:
        return type(f"HasPerm_{perm.replace('.', '_')}", (cls,), {"perm": perm, "safe_perm": safe_perm})

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        name = self.safe_perm if (self.safe_perm and request.method in SAFE_METHODS) else self.perm
        return user.has_perm(name)


class ReadOnlyForObservers(BasePermission):
    """Наблюдатель видит всё, но ничего не меняет."""

    message = "Наблюдатель может только просматривать"

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return request.method in SAFE_METHODS or user.has_perm("core.write")
