"""Дельта-синхронизация полевого устройства.

GET /api/sync/pull/?cursor=<мс> — всё, что изменилось с прошлого раза:
наряды (свои; диспетчеру — все открытые), поверки по ним, остановки
командировок. Ответ содержит новый курсор. Клиент кладёт строки в
IndexedDB поверх старых (upsert по id/client_id), поэтому повтор и
перекрытие окна безопасны: курсор берётся с запасом назад.

Отправка изменений с устройства — прежние идемпотентные эндпоинты по
client_id (apps/verification/api_field.py). Конфликт правок — «последний
пишет», кроме подписанного протокола: его измерения сервер не принимает
никогда (apps/verification/measurements.py).
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Device
from apps.core.permissions import HasPerm
from apps.sync.auth import DEVICE_CLAIM
from apps.verification.api_field import FieldVerificationSerializer
from apps.verification.api_workorders import WorkOrderSerializer
from apps.verification.models import Verification, WorkOrder, WorkOrderStatus

OVERLAP = dt.timedelta(seconds=5)
MAX_ROWS = 500


def _from_cursor(raw: str | None) -> dt.datetime | None:
    try:
        value = int(raw or 0)
    except ValueError:
        return None
    if value <= 0:
        return None
    return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)


def _to_cursor(moment: dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


class PullView(APIView):
    permission_classes = [HasPerm.of("verification.field_work", safe_perm="verification.field_work")]

    def get(self, request):
        started = timezone.now()
        since = _from_cursor(request.query_params.get("cursor"))
        employee = getattr(request.user, "employee", None)
        sees_all = request.user.has_perm("verification.manage_workorders")

        scope = WorkOrder.objects.all()
        if not sees_all:
            scope = scope.filter(assigned_employee=employee) if employee else scope.none()
        orders = scope.select_related("client", "site", "assigned_employee")
        if since is None:
            # Первая синхронизация: только то, с чем работать — открытые и
            # закрытые за последнюю неделю, а не вся история фирмы.
            orders = orders.filter(
                Q(status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.IN_PROGRESS])
                | Q(updated_at__gte=started - dt.timedelta(days=7))
            )
        else:
            orders = orders.filter(updated_at__gt=since)
        orders = list(orders.order_by("updated_at")[:MAX_ROWS])

        order_ids = [o.pk for o in orders]
        verifications = Verification.objects.select_related("instrument__si_type")
        if since is None:
            verifications = verifications.filter(work_order_id__in=order_ids)
        else:
            verifications = verifications.filter(work_order__in=scope, updated_at__gt=since)
        verifications = list(verifications.order_by("updated_at")[:MAX_ROWS])

        from apps.logistics.models import TripStop

        stops = TripStop.objects.select_related("trip")
        if not sees_all:
            stops = stops.filter(trip__employees=employee) if employee else stops.none()
        if since is not None:
            stops = stops.filter(updated_at__gt=since)
        else:
            stops = stops.filter(trip__date_to__gte=timezone.localdate() - dt.timedelta(days=1))
        stops = list(stops.order_by("updated_at")[:MAX_ROWS])

        truncated = len(orders) == MAX_ROWS or len(verifications) == MAX_ROWS or len(stops) == MAX_ROWS
        if truncated:
            # Не всё влезло — курсор по последней отданной строке, клиент придёт ещё раз.
            last = min(
                [rows[-1].updated_at for rows in (orders, verifications, stops) if len(rows) == MAX_ROWS]
            )
            cursor = _to_cursor(last - dt.timedelta(milliseconds=1))
        else:
            cursor = _to_cursor(started - OVERLAP)

        device_id = getattr(request.auth, "get", lambda *_: None)(DEVICE_CLAIM)
        if device_id:
            Device.objects.filter(device_id=device_id).update(last_sync_cursor=cursor, last_seen_at=started)

        return Response({
            "cursor": cursor,
            "more": truncated,
            "server_time": started,
            "work_orders": WorkOrderSerializer(orders, many=True).data,
            "verifications": [
                {**FieldVerificationSerializer(v).data, "work_order_id": v.work_order_id} for v in verifications
            ],
            "trip_stops": [
                {"id": s.pk, "trip_id": s.trip_id, "trip_title": s.trip.title, "work_order_id": s.work_order_id,
                 "day": s.day, "position": s.position, "eta": s.eta, "address": s.address, "status": s.status}
                for s in stops
            ],
        })


class DevicesView(APIView):
    """GET /api/sync/devices/ — мои устройства (видно, что и когда синхронизировалось)."""

    def get(self, request):
        employee = getattr(request.user, "employee", None)
        if employee is None:
            return Response([])
        return Response([
            {"id": d.pk, "label": d.label or d.device_id[:8], "last_seen_at": d.last_seen_at,
             "is_revoked": d.is_revoked, "created_at": d.created_at}
            for d in employee.devices.order_by("-last_seen_at")
        ])
