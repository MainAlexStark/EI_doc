"""Кто сейчас действует — чтобы не уведомлять человека о его же действии.

JWT разбирается в DRF, а не в middleware, поэтому пользователь запоминается
в аутентификации (ActorJWTAuthentication), а сбрасывается в начале каждого
запроса (ActorResetMiddleware): иначе анонимный запрос (форма на сайте) в том
же потоке gunicorn унаследовал бы чужого «действующего». В Celery и в
management-командах действующего нет — уведомление уходит всем адресатам.
"""

from __future__ import annotations

import contextvars

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.utils import datetime_from_epoch
from rest_framework_simplejwt.utils import get_md5_hash_password

_actor_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("notify_actor_id", default=None)


def current_actor_id() -> int | None:
    return _actor_id.get()


def set_actor(user_id: int | None) -> None:
    _actor_id.set(user_id)


def password_stamp_ok(token, user) -> bool:
    """Токен выдан при нынешнем пароле?

    Метка есть — сравниваем с хэшем пароля. Метки нет (выдан до этой версии) —
    принимаем, только если пароль с тех пор не менялся: выкатка не разлогинивает
    поверителей в командировке, но смена пароля закрывает и такие входы.
    При обновлении (StampedTokenRefreshSerializer) метка дописывается, так что
    токен без метки живёт не дольше одной ротации.
    """
    stamp = token.get(api_settings.REVOKE_TOKEN_CLAIM)
    if stamp is not None:
        return stamp == get_md5_hash_password(user.password)
    from apps.notify.models import AccountSecurity

    changed = AccountSecurity.objects.filter(user=user).values_list("password_changed_at", flat=True).first()
    if changed is None:
        return True
    iat = token.get("iat")
    return iat is not None and iat > changed.timestamp()


PASSWORD_CHANGED = _("Пароль сменили — войдите заново.")


class ActorJWTAuthentication(JWTAuthentication):
    """JWT + «действующий» для уведомлений + отказ токенам, выданным до смены пароля.

    CHECK_REVOKE_TOKEN включён в SIMPLE_JWT только ради метки пароля в токене;
    саму проверку делаем здесь — мягче штатной (см. password_stamp_ok).
    """

    def get_user(self, validated_token):
        user = _get_user_without_stamp(self, validated_token)
        if not password_stamp_ok(validated_token, user):
            raise AuthenticationFailed(PASSWORD_CHANGED, code="password_changed")
        return user

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            set_actor(result[0].pk)
        return result


def _get_user_without_stamp(auth: JWTAuthentication, token):
    try:
        user_id = token[api_settings.USER_ID_CLAIM]
    except KeyError as exc:
        raise AuthenticationFailed(_("Token contained no recognizable user identification")) from exc
    user = auth.user_model.objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
    if user is None:
        raise AuthenticationFailed(_("User not found"), code="user_not_found")
    if api_settings.CHECK_USER_IS_ACTIVE and not user.is_active:
        raise AuthenticationFailed(_("User is inactive"), code="user_inactive")
    return user


class StampedTokenRefreshSerializer(TokenRefreshSerializer):
    """Обновление токена: не проходит, если пароль сменили после выдачи;
    повёрнутый refresh получает метку пароля и записывается в OutstandingToken
    (штатный simplejwt 5.4 его туда не пишет — и закрыть его при смене
    пароля было бы нечем)."""

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])
        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        user = get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
        if user is None or not api_settings.USER_AUTHENTICATION_RULE(user):
            raise AuthenticationFailed(self.error_messages["no_active_account"], "no_active_account")
        if not password_stamp_ok(refresh, user):
            raise AuthenticationFailed(PASSWORD_CHANGED, code="password_changed")
        refresh[api_settings.REVOKE_TOKEN_CLAIM] = get_md5_hash_password(user.password)
        data = {"access": str(refresh.access_token)}
        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                refresh.blacklist()
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            OutstandingToken.objects.create(
                user=user, jti=refresh[api_settings.JTI_CLAIM], token=str(refresh),
                created_at=refresh.current_time, expires_at=datetime_from_epoch(refresh["exp"]),
            )
            data["refresh"] = str(refresh)
        return data


class ActorResetMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_actor(None)
        try:
            # Django-админка: пользователь из сессии известен сразу.
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                set_actor(user.pk)
            return self.get_response(request)
        finally:
            set_actor(None)
