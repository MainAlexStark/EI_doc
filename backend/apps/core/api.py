"""Список сотрудников — для выпадающих списков «назначить исполнителя»

на экранах заявок, нарядов и задач. Отдельный файл, а не часть views.py:
это API, не диагностика.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Employee


class EmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    tab_number = serializers.CharField()
    position = serializers.CharField()


class EmployeeListView(APIView):
    """GET /api/core/employees/ — только действующие, для выбора исполнителя."""

    def get(self, request):
        qs = Employee.objects.filter(is_active=True).order_by("full_name")
        return Response(EmployeeSerializer(qs, many=True).data)


class TelegramLinkCodeSerializer(serializers.Serializer):
    code = serializers.CharField()
    expires_at = serializers.DateTimeField()
    bot_username = serializers.CharField(allow_blank=True)
    already_linked = serializers.BooleanField()


class TelegramLinkCodeView(APIView):
    """POST /api/core/employees/me/telegram-link-code/ — новый код привязки Telegram.

    Только для своей учётной записи: сотрудник, у которого нет привязанного
    apps.core.Employee (например, наблюдатель без табельного номера), получает 404 —
    привязывать Telegram нечему.
    """

    def post(self, request):
        employee = getattr(request.user, "employee", None)
        if employee is None:
            raise NotFound("К вашей учётной записи не привязан сотрудник")
        code = employee.generate_telegram_link_code()
        return Response(TelegramLinkCodeSerializer({
            "code": code,
            "expires_at": employee.telegram_link_code_expires_at,
            "bot_username": getattr(settings, "TELEGRAM_BOT_USERNAME", ""),
            "already_linked": bool(employee.telegram_chat_id),
        }).data)
