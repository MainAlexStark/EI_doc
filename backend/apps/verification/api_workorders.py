"""Наряды: список диспетчера и смена статуса.

Отдельный файл от api.py (ввод измерений) и api_journal.py (журнал и
нормоконтроль) — наряд стоит до поверки в цепочке, у него своя аудитория:
диспетчер и сам исполнитель, не нормоконтролёр.
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import paging
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
    # Адрес командировки — тот же наряд; экран «Приёмки» по этим полям убирает
    # его из общего списка (десятки адресов одной поездки там только мешают).
    kind = serializers.CharField()
    client_id = serializers.IntegerField()
    client_inn = serializers.CharField(source="client.inn")
    client_kind = serializers.CharField(source="client.kind")
    client_address = serializers.CharField(source="client.legal_address")
    client_phone = serializers.CharField(source="client.phone")
    contact = serializers.CharField()
    received_at = serializers.DateTimeField(allow_null=True)
    issued_at = serializers.DateTimeField(allow_null=True)
    procurement_id = serializers.IntegerField(allow_null=True)
    procurement_label = serializers.SerializerMethodField()
    verifications_ready = serializers.SerializerMethodField()
    trip_id = serializers.SerializerMethodField()
    trip_title = serializers.SerializerMethodField()
    # Приёмка, привезённая с выезда («забрать коробку»), и обратный выезд «вернуть приборы».
    pickup = serializers.SerializerMethodField()
    delivery = serializers.SerializerMethodField()

    @staticmethod
    def _logistic(obj: WorkOrder, purpose: str) -> dict | None:
        stops = [s for s in obj.logistic_stops.all() if s.purpose == purpose]
        if not stops:
            return None
        stop = sorted(stops, key=lambda s: s.pk)[-1]
        return {"stop_id": stop.pk, "trip_id": stop.trip_id, "trip_title": stop.trip.title,
                "status": stop.status, "planned_date": stop.planned_date}

    def get_pickup(self, obj: WorkOrder) -> dict | None:
        return self._logistic(obj, "pickup") if obj.kind == "lab" else None

    def get_delivery(self, obj: WorkOrder) -> dict | None:
        return self._logistic(obj, "delivery") if obj.kind == "lab" else None

    def get_procurement_label(self, obj: WorkOrder) -> str:
        return f"{obj.procurement.label} · {obj.procurement.customer.name}" if obj.procurement_id else ""

    def get_verifications_ready(self, obj: WorkOrder) -> int:
        if hasattr(obj, "n_ready"):
            return obj.n_ready
        return obj.verifications.filter(status__in=["ready", "accepted"]).count()

    def get_trip_id(self, obj: WorkOrder) -> int | None:
        stop = getattr(obj, "trip_stop", None)
        return stop.trip_id if stop else None

    def get_trip_title(self, obj: WorkOrder) -> str:
        stop = getattr(obj, "trip_stop", None)
        return stop.trip.title if stop else ""

    def get_verifications_total(self, obj: WorkOrder) -> int:
        if hasattr(obj, "n_total"):
            return obj.n_total
        return obj.verifications.count()

    def get_verifications_accepted(self, obj: WorkOrder) -> int:
        if hasattr(obj, "n_accepted"):
            return obj.n_accepted
        return obj.verifications.filter(status="accepted").count()


def with_counts(qs):
    """Счётчики поверок одним запросом на весь список, а не по три на каждый наряд."""
    return qs.annotate(
        n_total=Count("verifications", distinct=True),
        n_ready=Count("verifications", filter=Q(verifications__status__in=["ready", "accepted"]), distinct=True),
        n_accepted=Count("verifications", filter=Q(verifications__status="accepted"), distinct=True),
    )


def visible_work_orders(user, qs):
    """Руководство и наблюдатель видят все наряды; поверитель — свои (назначенные ему) и адреса
    командировок, где он старший или участник (открыть наряд адреса из «Командировок»)."""
    from apps.core.models import Role
    from apps.logistics.dispatch import can_control

    if can_control(user) or getattr(user, "role", "") == Role.OBSERVER:
        return qs
    employee = getattr(user, "employee", None)
    if employee is None:
        return qs.none()
    mine = WorkOrder.objects.filter(
        Q(assigned_employee=employee)
        | Q(trip_stop__trip__lead=employee) | Q(trip_stop__trip__members=employee)
        | Q(logistic_stops__trip__lead=employee) | Q(logistic_stops__trip__members=employee)
    ).values("pk")
    return qs.filter(pk__in=mine)  # подзапрос, а не join: счётчики with_counts не размножаются


class WorkOrderListView(APIView):
    """GET /api/work-orders/?status=&assigned_employee=&date_from=&date_to=&id=&trip=&kind=&issued=

    Массив до ``limit`` (200) или страница при ``?page_size=`` — см. apps.core.paging.
    ``issued=0`` — приёмки, ещё не выданные заказчику. Поверитель видит только свои (``visible_work_orders``).
    """

    def get(self, request):
        qs = with_counts(WorkOrder.objects.select_related(
            "client", "site", "assigned_employee", "trip_stop__trip", "procurement__customer"
        ).prefetch_related("logistic_stops__trip"))
        qs = visible_work_orders(request.user, qs)
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        ids = [int(part) for part in request.query_params.get("id", "").split(",") if part.strip().isdigit()]
        if ids:
            # Точечный запрос (открыть наряд адреса командировки) — остальные фильтры не нужны.
            return Response(WorkOrderSerializer(qs.filter(pk__in=ids), many=True).data)
        trip_param = request.query_params.get("trip")
        if trip_param == "none":
            qs = qs.filter(trip_stop__isnull=True)
        elif trip_param:
            qs = qs.filter(trip_stop__trip_id=trip_param)
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
        issued = request.query_params.get("issued")
        if issued in ("0", "1"):
            qs = qs.filter(issued_at__isnull=issued == "0")
        return paging.respond(request, qs, lambda rows: WorkOrderSerializer(rows, many=True).data)


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
        from apps.logistics.service import attach_to_city_day

        attach_to_city_day(obj)  # с датой — сразу в день исполнителя во «Выездах»
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
