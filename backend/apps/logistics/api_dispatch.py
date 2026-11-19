"""API диспетчерской (руководство) и управления сотрудниками.

GET  /api/dispatch/overview/?date=            — «Панель»: итоги, что требует внимания, карточки сотрудников
GET  /api/dispatch/map/?date_from=&date_to=   — все адреса выездов на карте + очередь неназначенного
GET  /api/dispatch/schedule/?date_from=&date_to= — командный график: сотрудники × дни
POST /api/dispatch/assign/                    — назначить пачкой: {items:[{type,id}], employee_id, date?, time?}

GET  /api/core/staff/                         — сотрудники с учётными записями (всё руководство)
POST /api/core/staff/                         — завести (владелец/руководитель), отдаёт временный пароль
PATCH /api/core/staff/<id>/                   — поменять карточку, роль, почту, уволить/вернуть
POST /api/core/staff/<id>/password/           — новый временный пароль
"""

from __future__ import annotations

import datetime as dt

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import staff
from apps.core.models import Employee
from apps.logistics import dispatch


def _require_control(request) -> None:
    if not dispatch.can_control(request.user):
        raise PermissionDenied("Панель управления — для руководства, метролога и менеджера")


def _date(value: str | None, default: dt.date) -> dt.date:
    if not value:
        return default
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({"detail": f"Дата в формате YYYY-MM-DD: {value}"}) from exc


class OverviewView(APIView):
    def get(self, request):
        _require_control(request)
        return Response(dispatch.overview(_date(request.query_params.get("date"), timezone.localdate())))


class MapView(APIView):
    def get(self, request):
        _require_control(request)
        today = timezone.localdate()
        start = _date(request.query_params.get("date_from"), today)
        end = _date(request.query_params.get("date_to"), start)
        return Response(dispatch.map_points(start, end))


class ScheduleView(APIView):
    def get(self, request):
        _require_control(request)
        today = timezone.localdate()
        start = _date(request.query_params.get("date_from"), today - dt.timedelta(days=today.weekday()))
        end = _date(request.query_params.get("date_to"), start + dt.timedelta(days=13))
        return Response(dispatch.team_schedule(start, end))


class AssignItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["stop", "order", "request"])
    id = serializers.IntegerField()


class AssignSerializer(serializers.Serializer):
    items = AssignItemSerializer(many=True)
    employee_id = serializers.IntegerField()
    date = serializers.DateField(required=False, allow_null=True)
    time = serializers.TimeField(required=False, allow_null=True)


class AssignView(APIView):
    def post(self, request):
        _require_control(request)
        payload = AssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if not data["items"]:
            raise ValidationError({"detail": "Нечего назначать"})
        employee = get_object_or_404(Employee, pk=data["employee_id"], is_active=True)
        return Response(dispatch.assign_many(
            [dict(item) for item in data["items"]], employee, data.get("date"), data.get("time"),
        ))


# ---------------------------------------------------------------------------
# Сотрудники
# ---------------------------------------------------------------------------
class StaffWriteSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200, required=False)
    short_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    position = serializers.CharField(max_length=120, required=False, allow_blank=True)
    tab_number = serializers.CharField(max_length=8, required=False, allow_blank=True)
    email = serializers.CharField(max_length=254, required=False, allow_blank=True)
    role = serializers.CharField(max_length=20, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


def _staff_error(exc: staff.StaffError):
    return ValidationError({"detail": str(exc)})


class StaffListView(APIView):
    def get(self, request):
        _require_control(request)
        qs = Employee.objects.select_related("user").prefetch_related("attestations")
        if request.query_params.get("all") != "1":
            qs = qs.filter(is_active=True)
        return Response({
            "can_admin": staff.can_admin(request.user),
            "roles": staff.roles(),
            "next_tab_number": staff.next_tab_number(),
            "staff": [staff.row(e) for e in qs.order_by("-is_active", "full_name")],
        })

    def post(self, request):
        payload = StaffWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            employee, secret = staff.create(
                request.user, full_name=data.get("full_name", ""), position=data.get("position", ""),
                email=data.get("email", ""), role=data.get("role") or "verifier",
                tab_number=data.get("tab_number", ""), short=data.get("short_name", ""),
            )
        except staff.StaffError as exc:
            raise _staff_error(exc) from exc
        return Response({"employee": staff.row(employee), "password": secret}, status=201)


class StaffDetailView(APIView):
    def patch(self, request, pk: int):
        employee = get_object_or_404(Employee.objects.select_related("user"), pk=pk)
        payload = StaffWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        try:
            employee, secret = staff.update(request.user, employee, **payload.validated_data)
        except staff.StaffError as exc:
            raise _staff_error(exc) from exc
        return Response({"employee": staff.row(employee), "password": secret})


class StaffPasswordView(APIView):
    def post(self, request, pk: int):
        employee = get_object_or_404(Employee.objects.select_related("user"), pk=pk)
        try:
            secret = staff.reset_password(request.user, employee)
        except staff.StaffError as exc:
            raise _staff_error(exc) from exc
        return Response({"password": secret, "email": employee.user.email})
