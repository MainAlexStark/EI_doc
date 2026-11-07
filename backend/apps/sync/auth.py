"""Полевые устройства: токены с привязкой к device_id и отзыв одной кнопкой.

Телефон поверителя получает refresh-токен на 30 дней (вместо 7 у веба), чтобы
не разлогиниваться между командировками. Платой за это служит привязка:
в токене записан device_id, и отозванное в админке устройство теряет доступ
сразу — и access, и refresh перестают приниматься.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Device

DEVICE_CLAIM = "device_id"
DEVICE_REFRESH_LIFETIME = timedelta(days=30)


def device_state(device_id: str) -> Device | None:
    return Device.objects.filter(device_id=device_id).select_related("employee").first()


def check_device(device_id: str | None) -> None:
    if not device_id:
        return
    device = device_state(device_id)
    if device is None or device.is_revoked:
        raise AuthenticationFailed("Устройство отозвано — войдите заново с разрешённого устройства",
                                   code="device_revoked")


class DeviceJWTAuthentication(JWTAuthentication):
    """Обычная JWT-аутентификация + отказ токенам отозванных устройств."""

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        device_id = token.get(DEVICE_CLAIM)
        if device_id:
            check_device(device_id)
            Device.objects.filter(device_id=device_id).update(last_seen_at=timezone.now())
        return token


class DeviceTokenSerializer(TokenObtainPairSerializer):
    """Вход с устройства: email, пароль, device_id и название устройства."""

    device_id = serializers.CharField(max_length=64, min_length=8)
    label = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate(self, attrs):
        device_id = attrs.pop("device_id")
        label = attrs.pop("label", "")
        super().validate(attrs)  # проверка пароля, self.user
        employee = getattr(self.user, "employee", None)
        if employee is None:
            raise AuthenticationFailed("К учётной записи не привязан сотрудник — полевой вход недоступен")

        device = Device.objects.filter(device_id=device_id).first()
        if device is not None and device.is_revoked:
            raise AuthenticationFailed("Это устройство отозвано. Обратитесь к руководителю")
        if device is not None and device.employee_id != employee.pk:
            raise AuthenticationFailed("Устройство закреплено за другим сотрудником")
        if device is None:
            device = Device.objects.create(employee=employee, device_id=device_id, label=label)
        elif label and device.label != label:
            device.label = label
        device.last_seen_at = timezone.now()
        device.save()

        refresh = RefreshToken.for_user(self.user)
        refresh.set_exp(lifetime=DEVICE_REFRESH_LIFETIME)
        refresh[DEVICE_CLAIM] = device_id
        access = refresh.access_token
        access[DEVICE_CLAIM] = device_id
        return {"refresh": str(refresh), "access": str(access), "device": device.pk}


class DeviceAwareRefreshSerializer(TokenRefreshSerializer):
    """Обновление пары: отозванное устройство не получает новый access."""

    def validate(self, attrs):
        try:
            device_id = RefreshToken(attrs["refresh"]).get(DEVICE_CLAIM)
        except Exception as exc:
            raise InvalidToken(str(exc)) from exc
        check_device(device_id)
        data = super().validate(attrs)
        if device_id:
            # Новая пара наследует привязку (ROTATE_REFRESH_TOKENS) — дописываем claim.
            from rest_framework_simplejwt.tokens import AccessToken

            access = AccessToken(data["access"])
            access[DEVICE_CLAIM] = device_id
            data["access"] = str(access)
            if "refresh" in data:
                refresh = RefreshToken(data["refresh"])
                refresh[DEVICE_CLAIM] = device_id
                refresh.set_exp(lifetime=DEVICE_REFRESH_LIFETIME)
                data["refresh"] = str(refresh)
        return data
