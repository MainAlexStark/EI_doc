"""Календарь доступности сотрудников и публичная выдача слотов для формы заявки.

Отдельный файл от api.py (заявки) и от api_workorders.py (наряды): у
доступности своя аудитория — сам сотрудник (свой график) и анонимный
заявитель (агрегированные слоты, без имён и без чужих данных).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from django.utils import timezone
from rest_framework import generics, serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import District
from apps.core.models import Role
from apps.hub.models import AvailabilityKind, DistrictAssignment, EmployeeAvailability

MANAGE_ANY_ROLES = {Role.OWNER, Role.HEAD, Role.MANAGER}


def _time_str(value: dt.time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


class EmployeeAvailabilitySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)

    class Meta:
        model = EmployeeAvailability
        fields = [
            "id", "employee", "employee_name", "kind", "date",
            "start_time", "end_time", "is_priority", "note", "created_at",
        ]
        read_only_fields = ["created_at"]
        extra_kwargs = {"employee": {"required": False}}  # проставляется в perform_create

    def validate(self, data):
        start = data.get("start_time", getattr(self.instance, "start_time", None))
        end = data.get("end_time", getattr(self.instance, "end_time", None))
        if start and end and start >= end:
            raise serializers.ValidationError("Время начала должно быть раньше времени окончания")
        return data


class AvailabilityListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/hub/availability/

    Без ?employee= — свои слоты. С ?employee=<id> — чужие, доступно только
    владельцу, руководителю и менеджеру (диспетчеру нужно видеть график,
    когда он сам заводит наряд не из заявки с сайта).
    """

    serializer_class = EmployeeAvailabilitySerializer

    def get_queryset(self):
        qs = EmployeeAvailability.objects.select_related("employee").order_by("date", "start_time")
        employee_param = self.request.query_params.get("employee")
        me = getattr(self.request.user, "employee", None)
        if employee_param:
            if str(getattr(me, "id", None)) != str(employee_param) and self.request.user.role not in MANAGE_ANY_ROLES:
                raise PermissionDenied("Чужой график виден только руководителю и менеджеру")
            qs = qs.filter(employee_id=employee_param)
        else:
            qs = qs.filter(employee=me) if me else qs.none()
        date_from = self.request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs

    def perform_create(self, serializer):
        me = getattr(self.request.user, "employee", None)
        requested_employee = serializer.validated_data.get("employee")
        if requested_employee and requested_employee != me and self.request.user.role not in MANAGE_ANY_ROLES:
            raise PermissionDenied("Можно заводить слоты только в своём графике")
        serializer.save(employee=requested_employee or me)


class AvailabilityDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = EmployeeAvailabilitySerializer
    queryset = EmployeeAvailability.objects.select_related("employee")

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        me = getattr(request.user, "employee", None)
        if obj.employee_id != getattr(me, "id", None) and request.user.role not in MANAGE_ANY_ROLES:
            raise PermissionDenied("Чужой слот можно менять только руководителю и менеджеру")


# ---------------------------------------------------------------------------
# Публичные слоты для формы заявки — без авторизации, без имён сотрудников
# ---------------------------------------------------------------------------
class PublicSlotSerializer(serializers.Serializer):
    date = serializers.DateField()
    start_time = serializers.CharField(allow_null=True)
    end_time = serializers.CharField(allow_null=True)
    is_priority = serializers.BooleanField()


class AvailabilityPublicSlotsView(APIView):
    """GET /api/hub/availability/slots/?district=<название>&date_from=&date_to=

    Отдаёт только даты/время и признак приоритетности — без исполнителя:
    кто именно поедет, решает диспетчер при подтверждении заявки (routing
    только рекомендует, см. apps.hub.routing). Пустой список — по этому
    району пока никто не завёл график, форма покажет обычный date-picker
    и подпись, что дату согласует диспетчер по телефону.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        district_name = (request.query_params.get("district") or "").strip()
        date_from = request.query_params.get("date_from") or timezone.localdate().isoformat()
        date_to = request.query_params.get("date_to") or (
            timezone.localdate() + dt.timedelta(days=45)
        ).isoformat()

        district = District.objects.filter(name=district_name).first() if district_name else None
        if not district:
            return Response({"district_known": False, "slots": []})

        employee_ids = DistrictAssignment.objects.filter(
            district=district, is_active=True
        ).values_list("employee_id", flat=True)

        qs = (
            EmployeeAvailability.objects.filter(
                employee_id__in=list(employee_ids),
                kind=AvailabilityKind.DISTRICT,
                date__gte=date_from,
                date__lte=date_to,
            )
            .order_by("date", "start_time")
        )

        slots = [
            {
                "date": slot.date,
                "start_time": _time_str(slot.start_time),
                "end_time": _time_str(slot.end_time),
                "is_priority": slot.is_priority,
            }
            for slot in qs
        ]
        return Response({"district_known": True, "slots": PublicSlotSerializer(slots, many=True).data})
