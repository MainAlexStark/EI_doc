"""Список сотрудников — для выпадающих списков «назначить исполнителя»

на экранах заявок, нарядов и задач. Отдельный файл, а не часть views.py:
это API, не диагностика.
"""

from __future__ import annotations

from django.conf import settings
from apps.core.permissions import HasPerm, user_permissions
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


class EmployeeMeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    tab_number = serializers.CharField()
    position = serializers.CharField()
    telegram_linked = serializers.BooleanField()


class MeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.CharField()
    role_display = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())
    employee = EmployeeMeSerializer(allow_null=True)


class MeView(APIView):
    """GET /api/core/employees/me/ — кто сейчас в системе.

    Нужен, чтобы в шапке SPA показать имя сотрудника, а не просто кнопку
    «Выйти» — JWT-токен сам по себе имени не несёт. Если к учётной записи
    не привязан Employee (например, наблюдатель без табельного номера),
    employee будет null, и фронтенд покажет email вместо ФИО.
    """

    def get(self, request):
        user = request.user
        employee = getattr(user, "employee", None)
        data = {
            "email": user.email,
            "role": user.role,
            "role_display": user.get_role_display(),
            "permissions": user_permissions(user),
            "employee": None,
        }
        if employee is not None:
            data["employee"] = {
                "id": employee.id,
                "full_name": employee.full_name,
                "tab_number": employee.tab_number,
                "position": employee.position,
                "telegram_linked": bool(employee.telegram_chat_id),
            }
        return Response(MeSerializer(data).data)


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


class PersonalDataView(APIView):
    """Запрос субъекта ПДн (152-ФЗ): что о нём хранится и обезличивание.

    GET ?phone=&email= — найденные заявки и клиенты (действие журналируется);
    POST {phone, email, reason} — обезличить всё найденное.
    """

    permission_classes = [HasPerm.of("core.manage_personal_data")]

    def _subject(self, data):
        from apps.core import personal_data

        phone = str(data.get("phone", "")).strip()
        email = str(data.get("email", "")).strip()
        if len("".join(ch for ch in phone if ch.isdigit())) < 10 and "@" not in email:
            return None
        return personal_data.find_subject(phone=phone, email=email)

    def get(self, request):
        from apps.core.models import PersonalDataEvent

        found = self._subject(request.query_params)
        if found is None:
            return Response({"detail": "Нужен телефон (10 цифр) или email"}, status=400)
        for kind, rows in (("hub.request", found["requests"]), ("verification.client", found["clients"])):
            for row in rows:
                PersonalDataEvent.objects.create(action=PersonalDataEvent.Action.EXPORT, object_type=kind,
                                                 object_id=row["id"], performed_by=request.user,
                                                 note="просмотр по запросу субъекта")
        return Response(found)

    def post(self, request):
        from apps.core import personal_data
        from apps.core.models import PersonalDataEvent
        from apps.hub.models import Request
        from apps.verification.models import Client

        found = self._subject(request.data)
        if found is None:
            return Response({"detail": "Нужен телефон (10 цифр) или email"}, status=400)
        reason = str(request.data.get("reason", "")).strip() or "отзыв согласия субъектом"
        action = PersonalDataEvent.Action.ANONYMIZE_REQUEST
        requests_done = sum(
            personal_data.anonymize_request(r, action=action, note=reason, user=request.user)
            for r in Request.objects.filter(pk__in=[row["id"] for row in found["requests"]])
        )
        clients_done = sum(
            personal_data.anonymize_client(c, action=action, note=reason, user=request.user)
            for c in Client.objects.filter(pk__in=[row["id"] for row in found["clients"]])
        )
        return Response({"requests": requests_done, "clients": clients_done})
