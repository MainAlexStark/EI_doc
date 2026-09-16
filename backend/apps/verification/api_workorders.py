"""Наряды: список диспетчера и смена статуса.

Отдельный файл от api.py (ввод измерений) и api_journal.py (журнал и
нормоконтроль) — наряд стоит до поверки в цепочке, у него своя аудитория:
диспетчер и сам исполнитель, не нормоконтролёр.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.verification.models import WorkOrder, WorkOrderStatus


class WorkOrderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    status_display = serializers.CharField(source="get_status_display")
    client = serializers.CharField(source="client.name")
    site = serializers.CharField(source="site.address")
    assigned_employee = serializers.CharField(source="assigned_employee.full_name")
    assigned_employee_id = serializers.IntegerField()
    scheduled_date = serializers.DateField(allow_null=True)
    scheduled_time = serializers.TimeField(allow_null=True)
    note = serializers.CharField()
    request_id = serializers.IntegerField(allow_null=True)
    verifications_total = serializers.SerializerMethodField()
    verifications_accepted = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    closed_at = serializers.DateTimeField(allow_null=True)

    def get_verifications_total(self, obj: WorkOrder) -> int:
        return obj.verifications.count()

    def get_verifications_accepted(self, obj: WorkOrder) -> int:
        return obj.verifications.filter(status="accepted").count()


class WorkOrderListView(APIView):
    """GET /api/work-orders/?status=&assigned_employee=&date_from=&date_to="""

    def get(self, request):
        qs = WorkOrder.objects.select_related("client", "site", "assigned_employee").prefetch_related(
            "verifications"
        )
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        employee_param = request.query_params.get("assigned_employee")
        if employee_param:
            qs = qs.filter(assigned_employee_id=employee_param)
        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(scheduled_date__gte=date_from)
        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(scheduled_date__lte=date_to)
        return Response(WorkOrderSerializer(qs[:200], many=True).data)


class WorkOrderCreateSerializer(serializers.Serializer):
    client_id = serializers.IntegerField()
    site_id = serializers.IntegerField()
    employee_id = serializers.IntegerField(source="assigned_employee_id")
    scheduled_date = serializers.DateField(required=False, allow_null=True)
    scheduled_time = serializers.TimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)


class WorkOrderCreateView(APIView):
    """POST /api/work-orders/ — наряд напрямую, не из заявки с сайта."""

    @extend_schema(request=WorkOrderCreateSerializer, responses={201: WorkOrderSerializer})
    def post(self, request):
        payload = WorkOrderCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        obj = WorkOrder.objects.create(**payload.validated_data)
        return Response(WorkOrderSerializer(obj).data, status=201)


class WorkOrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=WorkOrderStatus.choices)


class WorkOrderStatusView(APIView):
    """POST /api/work-orders/{id}/status/ — ручная смена статуса.

    Обычно статус пересчитывается сам сигналом при закрытии поверок
    (apps.verification.signals); эта ручка — для отмены наряда и для
    возврата из отмены, чего сигнал не делает. Оба случая — ручная отмена и
    ручной возврат — дальше отражаются на связанной заявке через
    ``sync_request_status()`` (см. WorkOrder.refresh_status() docstring):
    без этого вызова заявка при отменённом наряде так и оставалась бы
    «Подтверждена», хотя по факту работа не состоится.
    """

    @extend_schema(request=WorkOrderStatusSerializer, responses={200: WorkOrderSerializer})
    def post(self, request, pk: int):
        obj = get_object_or_404(WorkOrder, pk=pk)
        payload = WorkOrderStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        obj.status = payload.validated_data["status"]
        obj.closed_at = None if obj.status != WorkOrderStatus.DONE else obj.closed_at
        obj.save(update_fields=["status", "closed_at"])
        obj.sync_request_status()
        return Response(WorkOrderSerializer(obj).data)
