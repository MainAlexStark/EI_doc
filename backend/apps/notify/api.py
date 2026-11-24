"""Смена и сброс пароля, настройки уведомлений — API для кабинета.

Пароль — как в openseller (cmd/core/account.go changePassword, auth.go forgot/reset):
* смена: текущий пароль + новый дважды; после смены остальные входы закрыты,
  этому устройству — новые токены; письмо «пароль изменён»;
* «Забыли пароль?»: ответ одинаковый, есть такой адрес или нет; ссылка живёт
  час и срабатывает один раз (Django PasswordResetTokenGenerator: токен
  привязан к хэшу пароля и времени входа — после смены он мёртв);
* без настроенной почты честно говорим, что письмо не придёт.
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.models import Role
from apps.core.staff import can_admin
from apps.notify import outbox, passwords
from apps.notify.models import Category, NotifyPrefs, OPTIONAL_CATEGORIES
from apps.notify.render import absolute
from apps.notify.signals import REQUEST_RECIPIENTS

logger = logging.getLogger(__name__)

RESET_TTL_TEXT = "один час"
BAD_LINK = "Ссылка не сработала: она устарела или уже использована. Запросите новую."


def _name(user) -> str:
    employee = getattr(user, "employee", None)
    if employee is None:
        return ""
    parts = employee.full_name.split()
    return parts[1] if len(parts) > 1 else employee.full_name


def notify_password_changed(user, how: str) -> None:
    outbox.enqueue("password_changed", user.email, {
        "name": _name(user), "how": how, "login_url": absolute("/"),
    }, user=user)


class _Throttled(APIView):
    throttle_classes = [ScopedRateThrottle]


# --------------------------------------------------------------------------
# Смена своего пароля
# --------------------------------------------------------------------------
class ChangePasswordSerializer(serializers.Serializer):
    current = serializers.CharField(trim_whitespace=False)
    password = serializers.CharField(trim_whitespace=False)
    password2 = serializers.CharField(trim_whitespace=False)


class PasswordChangeView(_Throttled):
    """POST /api/auth/password/change/ — {current, password, password2} → {access, refresh, closed}."""

    throttle_scope = "password_change"

    def post(self, request):
        data = ChangePasswordSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(data.validated_data["current"]):
            return Response({"current": ["Текущий пароль не тот."]}, status=status.HTTP_400_BAD_REQUEST)
        password = data.validated_data["password"]
        if password != data.validated_data["password2"]:
            return Response({"password2": ["Новые пароли не совпадают."]}, status=status.HTTP_400_BAD_REQUEST)
        if password == data.validated_data["current"]:
            return Response({"password": ["Новый пароль совпадает с текущим."]}, status=status.HTTP_400_BAD_REQUEST)
        errors = passwords.check(password, user)
        if errors:
            return Response({"password": errors}, status=status.HTTP_400_BAD_REQUEST)
        closed = passwords.set_password(user, password)
        notify_password_changed(user, "в профиле EI_doc")
        return Response({
            **passwords.new_tokens(user), "closed": closed,
            "detail": "Пароль изменён. Остальные входы закрыты, этот остался.",
        })


# --------------------------------------------------------------------------
# «Забыли пароль?»
# --------------------------------------------------------------------------
class ForgotSerializer(serializers.Serializer):
    email = serializers.EmailField()


def make_reset_token(user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return f"{uid}.{default_token_generator.make_token(user)}"


def user_for_token(token: str):
    uid, _, secret = (token or "").partition(".")
    if not uid or not secret:
        return None
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        user = get_user_model().objects.filter(pk=pk, is_active=True).first()
    except (TypeError, ValueError, OverflowError):
        return None
    if user is None or not default_token_generator.check_token(user, secret):
        return None
    return user


class PasswordForgotView(_Throttled):
    """POST /api/auth/password/forgot/ — {email}. Ответ одинаковый, есть адрес или нет."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "password_forgot"

    def post(self, request):
        data = ForgotSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        email = data.validated_data["email"].strip().lower()
        if not outbox.mail_configured():
            return Response({
                "mail_configured": False,
                "detail": "Письмо отправить не получится: на сервере не настроена почта. "
                          "Попросите руководителя сбросить пароль в «Сотрудниках».",
            })
        user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
        if user is not None and user.has_usable_password() and _address_allowance(email):
            outbox.enqueue("password_reset", user.email, {
                "name": _name(user), "link": absolute(f"/#reset={make_reset_token(user)}"), "ttl": RESET_TTL_TEXT,
            }, user=user)
        return Response({
            "mail_configured": True,
            "detail": f"Если адрес {email} есть в EI_doc, на него придёт ссылка для нового пароля. "
                      f"Она действует {RESET_TTL_TEXT}.",
        })


def _address_allowance(email: str) -> bool:
    """Не больше PASSWORD_FORGOT_PER_ADDRESS писем сброса на один адрес в час —
    молча (ответ тот же): чужой не завалит ящик письмами, сменяя IP."""
    limit = getattr(settings, "PASSWORD_FORGOT_PER_ADDRESS", 3)
    key = "notify:forgot:" + hashlib.sha256(email.encode()).hexdigest()
    cache.add(key, 0, timeout=60 * 60)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=60 * 60)
        count = 1
    return count <= limit


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class ResetSerializer(TokenSerializer):
    password = serializers.CharField(trim_whitespace=False)
    password2 = serializers.CharField(trim_whitespace=False)


class PasswordResetCheckView(_Throttled):
    """POST /api/auth/password/reset/check/ — {token} → {ok, email}: показать форму или «ссылка не сработала»."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "password_reset"

    def post(self, request):
        data = TokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = user_for_token(data.validated_data["token"])
        if user is None:
            return Response({"ok": False, "detail": BAD_LINK})
        return Response({"ok": True, "email": user.email})


class PasswordResetView(_Throttled):
    """POST /api/auth/password/reset/ — {token, password, password2}."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "password_reset"

    def post(self, request):
        data = ResetSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = user_for_token(data.validated_data["token"])
        if user is None:
            return Response({"detail": BAD_LINK, "code": "bad_link"}, status=status.HTTP_400_BAD_REQUEST)
        password = data.validated_data["password"]
        if password != data.validated_data["password2"]:
            return Response({"password2": ["Пароли не совпадают."]}, status=status.HTTP_400_BAD_REQUEST)
        errors = passwords.check(password, user)
        if errors:
            return Response({"password": errors}, status=status.HTTP_400_BAD_REQUEST)
        passwords.set_password(user, password)  # ссылка гаснет сама: токен привязан к хэшу пароля
        notify_password_changed(user, "по ссылке из письма «Сброс пароля»")
        return Response({"email": user.email, "detail": "Пароль изменён — войдите с новым. Остальные входы закрыты."})


# --------------------------------------------------------------------------
# Настройки уведомлений
# --------------------------------------------------------------------------
def available_categories(user) -> list[str]:
    has_employee = getattr(user, "employee", None) is not None
    out = []
    for category in OPTIONAL_CATEGORIES:
        if category == Category.REQUESTS:
            ok = user.is_superuser or user.role in REQUEST_RECIPIENTS
        elif category == Category.REVIEW:
            ok = has_employee or user.role == Role.METROLOGIST
        else:
            ok = has_employee
        if ok:
            out.append(category)
    return out


class PrefsView(APIView):
    """GET/PUT /api/notify/prefs/ — что присылать на почту: {categories: {requests: true, …}}."""

    def _payload(self, user):
        prefs = NotifyPrefs.objects.filter(user=user).first()
        employee = getattr(user, "employee", None)
        return {
            "email": user.email,
            "mail_configured": outbox.mail_configured(),
            "telegram_linked": bool(employee and employee.telegram_chat_id),
            "can_test": can_admin(user),
            "categories": [
                {"key": c.value, "label": c.label, "enabled": True if prefs is None else getattr(prefs, c.value)}
                for c in OPTIONAL_CATEGORIES if c in available_categories(user)
            ],
        }

    def get(self, request):
        return Response(self._payload(request.user))

    def put(self, request):
        values = (request.data or {}).get("categories") or {}
        if not isinstance(values, dict):
            return Response({"categories": ["Ожидается {категория: true/false}"]}, status=status.HTTP_400_BAD_REQUEST)
        prefs, _ = NotifyPrefs.objects.get_or_create(user=request.user)
        changed = []
        for category in OPTIONAL_CATEGORIES:
            if category.value in values:
                setattr(prefs, category.value, bool(values[category.value]))
                changed.append(category.value)
        if changed:
            prefs.save(update_fields=changed)
        return Response(self._payload(request.user))


class TestMailView(APIView):
    """POST /api/notify/test/ — пробное письмо себе (владелец и руководитель: проверить SMTP)."""

    def post(self, request):
        if not can_admin(request.user):
            return Response({"detail": "Пробное письмо отправляют владелец и руководитель"}, status=status.HTTP_403_FORBIDDEN)
        if not request.user.email:
            return Response({"detail": "У вашей учётной записи нет почты"}, status=status.HTTP_400_BAD_REQUEST)
        outbox.enqueue("test", request.user.email, {"name": _name(request.user)}, user=request.user)
        detail = (f"Письмо поставлено в очередь на {request.user.email}." if outbox.mail_configured()
                  else "Почта не настроена (SMTP_HOST пуст) — письмо только записано в журнал сервера.")
        return Response({"detail": detail, "mail_configured": outbox.mail_configured()})
