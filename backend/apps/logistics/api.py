"""API командировок: спрос, командировки, остановки, маршрут, эталоны, пакет."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasPerm
from apps.logistics import service
from apps.logistics.models import StandardReservation, StopStatus, Trip, TripStatus, TripStop

PLAN = [HasPerm.of("logistics.plan_trips", safe_perm="logistics.view_trips")]


def _f(value):
    return float(value) if value is not None else None


def stop_data(stop: TripStop) -> dict:
    wo = stop.work_order
    return {
        "id": stop.pk, "address": stop.address, "lat": _f(stop.latitude), "lon": _f(stop.longitude),
        "day": stop.day, "position": stop.position, "eta": stop.eta,
        "window_from": stop.window_from, "window_to": stop.window_to,
        "service_minutes": stop.service_minutes, "status": stop.status,
        "status_display": stop.get_status_display(), "note": stop.note,
        "work_order": wo.pk if wo else None,
        "work_order_status": wo.status if wo else None,
        "client": str(wo.client) if wo else "",
        "restricted": bool(wo and wo.site.is_restricted),
    }


def trip_data(trip: Trip, *, full: bool = False) -> dict:
    data = {
        "id": trip.pk, "title": trip.title, "status": trip.status, "status_display": trip.get_status_display(),
        "district": trip.district.name if trip.district_id else "", "district_id": trip.district_id,
        "date_from": trip.date_from, "date_to": trip.date_to,
        "employees": [{"id": e.pk, "name": e.full_name} for e in trip.employees.all()],
        "vehicle": trip.vehicle, "budget": trip.budget, "actual_cost": trip.actual_cost, "note": trip.note,
        "stops_count": trip.stops.count(),
    }
    if full:
        data["stops"] = [stop_data(s) for s in trip.stops.select_related("work_order__client", "work_order__site")]
        data["reservations"] = [
            {"id": r.pk, "standard": r.standard_id, "name": r.standard.name, "number": r.standard.fif_number,
             "date_from": r.date_from, "date_to": r.date_to}
            for r in trip.reservations.select_related("standard")
        ]
        data["plan_fact"] = service.plan_fact(trip)
        start = service.depot(trip)
        data["depot"] = {"lat": start[0], "lon": start[1]} if start else None
    return data


class MapConfigView(APIView):
    def get(self, request):
        return Response({"tile_url": settings.MAP_TILE_URL, "attribution": settings.MAP_TILE_ATTRIBUTION,
                         "depot": dict(zip(("lat", "lon"), service.depot())) if service.depot() else None})


class DemandView(APIView):
    """GET /api/logistics/demand/?days=60 — спрос по районам для планирования."""

    permission_classes = PLAN

    def get(self, request):
        try:
            days = max(1, min(int(request.query_params.get("days", 60)), 365))
        except ValueError:
            days = 60
        return Response({"days": days, "groups": service.demand(days)})


class TripSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    district_id = serializers.IntegerField(required=False, allow_null=True)
    employee_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    work_order_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    vehicle = serializers.CharField(required=False, allow_blank=True)
    budget = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    actual_cost = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=TripStatus.choices, required=False)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_to"] < attrs["date_from"]:
            raise serializers.ValidationError("Командировка заканчивается раньше, чем начинается")
        return attrs


def _apply(trip: Trip, data: dict) -> Trip:
    for name in ("title", "date_from", "date_to", "district_id", "vehicle", "budget", "actual_cost", "note", "status"):
        if name in data:
            setattr(trip, name, data[name])
    trip.save()
    if "employee_ids" in data:
        trip.employees.set(data["employee_ids"])
    if data.get("work_order_ids"):
        service.add_work_orders(trip, data["work_order_ids"])
    return trip


class TripListView(APIView):
    permission_classes = PLAN

    def get(self, request):
        qs = service.trips_for(request.user).select_related("district")
        if request.query_params.get("active"):
            qs = qs.exclude(status__in=[TripStatus.DONE, TripStatus.CANCELLED])
        return Response([trip_data(t) for t in qs[:200]])

    def post(self, request):
        payload = TripSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        trip = _apply(Trip(created_by=request.user), payload.validated_data)
        return Response(trip_data(trip, full=True), status=status.HTTP_201_CREATED)


class TripDetailView(APIView):
    permission_classes = PLAN

    def get(self, request, pk: int):
        trip = get_object_or_404(service.trips_for(request.user), pk=pk)
        service.refresh_stop_statuses(trip)
        return Response(trip_data(trip, full=True))

    def patch(self, request, pk: int):
        trip = get_object_or_404(Trip, pk=pk)
        payload = TripSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        return Response(trip_data(_apply(trip, payload.validated_data), full=True))


class TripOptimizeView(APIView):
    """POST /api/logistics/trips/{id}/optimize/ — разложить по дням и выстроить маршрут."""

    permission_classes = [HasPerm.of("logistics.plan_trips")]

    def post(self, request, pk: int):
        trip = get_object_or_404(Trip, pk=pk)
        try:
            summary = service.optimize(trip)
        except service.TripError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"summary": summary, "trip": trip_data(trip, full=True)})


class TripReserveSerializer(serializers.Serializer):
    standard_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class TripReserveView(APIView):
    """POST — забронировать эталоны; DELETE ?standard= — снять бронь."""

    permission_classes = [HasPerm.of("logistics.plan_trips")]

    def post(self, request, pk: int):
        trip = get_object_or_404(Trip, pk=pk)
        payload = TripReserveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        refused = service.reserve(trip, payload.validated_data["standard_ids"])
        return Response({"refused": refused, "trip": trip_data(trip, full=True)})

    def delete(self, request, pk: int):
        StandardReservation.objects.filter(trip_id=pk, standard_id=request.query_params.get("standard")).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StopSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=StopStatus.choices, required=False)
    day = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=250)
    service_minutes = serializers.IntegerField(required=False, min_value=5, max_value=600)


class TripStopView(APIView):
    """PATCH — отметка факта поверителем или перенос в другой день; DELETE — убрать из командировки."""

    permission_classes = [HasPerm.of("verification.field_work")]

    def patch(self, request, pk: int):
        stop = get_object_or_404(TripStop.objects.select_related("trip"), pk=pk)
        if not request.user.has_perm("logistics.plan_trips"):
            employee = getattr(request.user, "employee", None)
            if employee is None or not stop.trip.employees.filter(pk=employee.pk).exists():
                return Response({"detail": "Это не ваша командировка"}, status=status.HTTP_403_FORBIDDEN)
        payload = StopSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        for name, value in payload.validated_data.items():
            setattr(stop, name, value)
        stop.save()
        return Response(stop_data(stop))

    def delete(self, request, pk: int):
        if not request.user.has_perm("logistics.plan_trips"):
            return Response({"detail": "Недостаточно прав"}, status=status.HTTP_403_FORBIDDEN)
        get_object_or_404(TripStop, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TripPackView(APIView):
    """GET — PDF-пакет на выезд: маршрутный лист и бланки на режимные объекты."""

    permission_classes = PLAN

    def get(self, request, pk: int):
        from apps.verification.render import RenderError

        trip = get_object_or_404(service.trips_for(request.user), pk=pk)
        try:
            pdf = service.render_pack(trip)
        except RenderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="trip-{trip.pk}.pdf"'
        return response


class StandardOptionsView(APIView):
    """GET /api/logistics/standards/?date_from=&date_to= — эталоны в работе и занятость на даты."""

    permission_classes = PLAN

    def get(self, request):
        from apps.catalog.models import Standard

        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to") or date_from
        rows = []
        for standard in Standard.objects.filter(is_active=True).order_by("name"):
            busy = []
            if date_from:
                busy = [
                    {"trip": r.trip_id, "title": r.trip.title}
                    for r in StandardReservation.objects.filter(
                        standard=standard, date_from__lte=date_to, date_to__gte=date_from,
                    ).exclude(trip__status=TripStatus.CANCELLED).select_related("trip")
                ]
            rows.append({"id": standard.pk, "name": standard.name, "number": standard.fif_number,
                         "valid_until": standard.valid_until, "busy": busy})
        return Response(rows)
