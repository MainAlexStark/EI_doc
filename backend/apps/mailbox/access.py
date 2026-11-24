"""Кто видит почту фирмы: владелец, руководитель, менеджер (наблюдатель — нет)."""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.core.models import Role

MAIL_ROLES = {Role.OWNER, Role.HEAD, Role.MANAGER}


def can_mail(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and (user.is_superuser or user.role in MAIL_ROLES))


def mail_users() -> list:
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    return list(get_user_model().objects.filter(is_active=True).filter(Q(role__in=list(MAIL_ROLES)) | Q(is_superuser=True)))


class CanMail(BasePermission):
    message = "Почта фирмы — для владельца, руководителя и менеджеров"

    def has_permission(self, request, view) -> bool:
        return can_mail(request.user)
