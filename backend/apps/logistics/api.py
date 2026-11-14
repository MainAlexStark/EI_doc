"""API командировок и закупок (claude/trips.md).

    GET/POST          /api/trips/                       список / завести
    GET/PATCH/DELETE  /api/trips/{id}/                  карточка с адресами и сводкой
    POST              /api/trips/{id}/stops/            адрес (из плана или на месте)
    POST              /api/trips/{id}/stops/bulk/       адреса списком, по строке на адрес
    POST              /api/trips/{id}/stops/reorder/    порядок объезда
    POST              /api/trips/{id}/locate/           найти координаты адресам без точки
    POST              /api/trips/{id}/next-visit/       следующий заход с неотработанными адресами
    PATCH/DELETE      /api/trip-stops/{id}/             отметка, заметка, точка / убрать

    GET/POST          /api/procurements/                закупки
    GET/PATCH         /api/procurements/{id}/           карточка: объём, документы, пропуска, заходы
    POST              /api/procurements/{id}/documents/ загрузить файл
    GET/DELETE        /api/procurement-documents/{id}/  скачать / удалить
    POST              /api/procurements/{id}/passes/    пропуск
    PATCH/DELETE      /api/passes/{id}/

Кто что видит: руководители — всё; поверитель — командировки, где он старший
или участник, и закупки этих командировок.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django_sendfile import sendfile
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Employee, Role
from apps.logistics import geocode, planner, service
from apps.logistics.models import (
    DocumentKind,
    Pass,
    PassStatus,
    Procurement,
    ProcurementDocument,
    ProcurementItem,
    ProcurementLaw,
    ProcurementStatus,
    StopStatus,
    Trip,
    TripKind,
    TripStatus,
    TripStop,
)
from apps.verification.models import Client, ClientKind, Site

MANAGER_ROLES = {Role.OWNER, Role.HEAD, Role.METROLOGIST, Role.MANAGER}


# ---------------------------------------------------------------------------
# Права
# ---------------------------------------------------------------------------
def is_manager(user) -> bool:
    return bool(user.is_superuser or user.role in MANAGER_ROLES)


def employee_of(user) -> Employee | None:
    return getattr(user, "employee", None)


def visible_trips(user):
    qs = Trip.objects.select_related("lead", "procurement__customer").prefetch_related("members")
    if is_manager(user) or user.role == Role.OBSERVER:
        return qs
    employee = employee_of(user)
    if employee is None:
        return qs.none()
    return qs.filter(Q(lead=employee) | Q(members=employee)).distinct()


def visible_procurements(user):
    qs = Procurement.objects.select_related("customer", "responsible")
    if is_manager(user) or user.role == Role.OBSERVER:
        return qs
    employee = employee_of(user)
    if employee is None:
        return qs.none()
    return qs.filter(
        Q(trips__lead=employee) | Q(trips__members=employee) | Q(responsible=employee)
    ).distinct()


def can_edit_trip(user, trip: Trip) -> bool:
    if is_manager(user):
        return True
    employee = employee_of(user)
    return employee is not None and (
        trip.lead_id == employee.pk or trip.members.filter(pk=employee.pk).exists()
    )


def require_trip(request, pk: int, *, write: bool = False) -> Trip:
    trip = get_object_or_404(visible_trips(request.user), pk=pk)
    if write and not can_edit_trip(request.user, trip):
        raise PermissionDenied("Менять командировку могут её участники и руководители")
    return trip


def require_manager(request) -> None:
    if not is_manager(request.user):
        raise PermissionDenied("Закупки ведут руководители и менеджеры")


def guarded(action):
    """TripError — это отказ по смыслу, человеку нужен текст, а не 500."""
    try:
        return action()
    except service.TripError as exc:
        raise ValidationError({"detail": str(exc)}) from exc


# ---------------------------------------------------------------------------
# Представление
# ---------------------------------------------------------------------------
def person(employee: Employee | None) -> dict | None:
    return None if employee is None else {"id": employee.pk, "full_name": employee.full_name}


def procurement_brief(item: Procurement | None) -> dict | None:
    if item is None:
        return None
    return {
        "id": item.pk,
        "label": item.label,
        "number": item.number,
        "law": item.law,
        "law_display": item.get_law_display(),
        "title": item.title,
        "customer": item.customer.name,
        "contract_number": item.contract_number,
        "contract_date": item.contract_date,
        "deadline": item.deadline,
        "status": item.status,
        "status_display": item.get_status_display(),
    }


def trip_brief(trip: Trip, counts: dict | None = None) -> dict:
    counts = counts or {}
    return {
        "id": trip.pk,
        "kind": trip.kind,
        "kind_display": trip.get_kind_display(),
        "title": trip.title,
        "destination": trip.destination,
        "date_start": trip.date_start,
        "date_end": trip.date_end,
        "status": trip.status,
        "status_display": trip.get_status_display(),
        "lead": person(trip.lead),
        "members": [person(member) for member in trip.members.all()],
        "note": trip.note,
        "procurement": procurement_brief(trip.procurement),
        "stops_total": counts.get("stops_total", 0),
        "stops_done": counts.get("stops_done", 0),
        "instruments_entered": counts.get("instruments_entered", 0),
    }


def stop_data(stop: TripStop) -> dict:
    site = stop.site
    return {
        "id": stop.pk,
        "position": stop.position,
        "status": stop.status,
        "status_display": stop.get_status_display(),
        "site_id": site.pk,
        "address": site.address,
        "latitude": None if site.latitude is None else float(site.latitude),
        "longitude": None if site.longitude is None else float(site.longitude),
        "is_restricted": site.is_restricted,
        "access_note": site.access_note,
        "client": stop.client.name,
        "contact_name": stop.contact_name,
        "contact_phone": stop.contact_phone,
        "planned_date": stop.planned_date,
        "planned_count": stop.planned_count,
        "note": stop.note,
        "added_on_site": stop.added_on_site,
        "carried_from": stop.carried_from.trip.title if stop.carried_from_id else "",
        "visited_at": stop.visited_at,
        "window_start": stop.window_start.strftime("%H:%M") if stop.window_start else None,
        "window_end": stop.window_end.strftime("%H:%M") if stop.window_end else None,
        "service_minutes": stop.service_minutes,
        "eta": stop.eta,
        "drive_minutes": round(stop.drive_seconds / 60) if stop.drive_seconds is not None else None,
        "work_order_id": stop.work_order_id,
        "verifications_total": getattr(stop, "verifications_total", 0),
        "verifications_ready": getattr(stop, "verifications_ready", 0),
        "verifications_unsuitable": getattr(stop, "verifications_unsuitable", 0),
    }


def trip_detail(trip: Trip, user) -> dict:
    stops = list(service.stops_with_counts(trip))
    summary = service.summarize(stops)
    data = trip_brief(trip, summary)
    data.update(
        stops=[stop_data(stop) for stop in stops],
        summary=summary,
        can_edit=can_edit_trip(user, trip),
        can_locate=geocode.is_enabled(),
        route=trip.route or None,
        routing_traffic=bool(getattr(settings, "DGIS_API_KEY", "")),
        work_day=[t.strftime("%H:%M") for t in planner.work_day()],
        has_base=bool(getattr(settings, "ROUTE_BASE_ADDRESS", "") or getattr(settings, "ROUTE_BASE_LAT", None)),
        visit_number=trip.visit_number,
        passes=service.pass_coverage(trip)
        if trip.procurement_id and trip.procurement.passes.exists() else [],
    )
    return data


def document_data(doc: ProcurementDocument) -> dict:
    return {
        "id": doc.pk,
        "kind": doc.kind,
        "kind_display": doc.get_kind_display(),
        "title": doc.title,
        "filename": doc.file.name.rsplit("/", 1)[-1] if doc.file else "",
        "size": _size(doc),
        "uploaded_by": doc.uploaded_by.full_name if doc.uploaded_by_id else "",
        "created_at": doc.created_at,
    }


def _size(doc: ProcurementDocument) -> int:
    try:
        return doc.file.size
    except (OSError, ValueError):
        return 0


def pass_data(item: Pass) -> dict:
    return {
        "id": item.pk,
        "employee": person(item.employee) if item.employee_id else None,
        "vehicle": item.vehicle,
        "holder": item.holder,
        "site_id": item.site_id,
        "site": item.site.address if item.site_id else "",
        "number": item.number,
        "valid_from": item.valid_from,
        "valid_to": item.valid_to,
        "status": item.status,
        "status_display": item.get_status_display(),
        "note": item.note,
    }


def procurement_detail(item: Procurement, user) -> dict:
    data = procurement_brief(item)
    trips = list(
        visible_trips(user).filter(procurement=item).order_by("date_start", "id")
    )
    counts = service.trip_counts([trip.pk for trip in trips])
    data.update(
        customer_id=item.customer_id,
        customer_inn=item.customer.inn,
        date_start=item.date_start,
        amount=item.amount,
        eis_url=item.eis_url,
        open_url=item.search_url,
        responsible=person(item.responsible),
        contact=item.contact,
        note=item.note,
        items=[{"id": row.pk, "title": row.title, "quantity": row.quantity} for row in item.items.all()],
        documents=[document_data(doc) for doc in item.documents.select_related("uploaded_by")],
        passes=[pass_data(row) for row in item.passes.select_related("employee", "site")],
        sites=[
            {"id": site.pk, "address": site.address, "is_restricted": site.is_restricted}
            for site in Site.objects.filter(client=item.customer).order_by("address")
        ],
        trips=[trip_brief(trip, counts.get(trip.pk)) for trip in trips],
        progress=service.procurement_progress(item),
        can_edit=is_manager(user),
    )
    return data


# ---------------------------------------------------------------------------
# Командировки
# ---------------------------------------------------------------------------
class TripWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=TripKind.choices, required=False)
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    destination = serializers.CharField(max_length=200, required=False, allow_blank=True)
    date_start = serializers.DateField(required=False, allow_null=True)
    date_end = serializers.DateField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=TripStatus.choices, required=False)
    lead_id = serializers.IntegerField(required=False)
    member_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    procurement_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        start, end = attrs.get("date_start"), attrs.get("date_end")
        if start and end and end < start:
            raise serializers.ValidationError("Возвращение раньше выезда")
        return attrs


class TripListView(APIView):
    def get(self, request):
        qs = visible_trips(request.user)
        state = request.query_params.get("state")
        if state == "open":
            qs = qs.filter(status__in=[TripStatus.PLANNED, TripStatus.ACTIVE])
        elif state == "closed":
            qs = qs.filter(status__in=[TripStatus.DONE, TripStatus.CANCELLED])
        procurement = request.query_params.get("procurement")
        if procurement:
            qs = qs.filter(procurement_id=procurement)
        trips = list(qs[:200])
        counts = service.trip_counts([trip.pk for trip in trips])
        return Response([trip_brief(trip, counts.get(trip.pk)) for trip in trips])

    def post(self, request):
        payload = TripWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        user = request.user
        me = employee_of(user)

        lead = me
        if data.get("lead_id") and (is_manager(user) or (me and data["lead_id"] == me.pk)):
            lead = get_object_or_404(Employee, pk=data["lead_id"], is_active=True)
        if lead is None:
            raise ValidationError({"detail": "Выберите старшего — у вашей учётной записи нет карточки сотрудника"})

        procurement = None
        if data.get("procurement_id"):
            procurement = get_object_or_404(visible_procurements(user), pk=data["procurement_id"])

        title = (data.get("title") or "").strip()
        if not title:
            if procurement:
                number = procurement.trips.count() + 1
                title = f"{procurement.customer.name} — заход {number}"
            else:
                title = (data.get("destination") or "").strip() or "Командировка"

        kind = TripKind.PROCUREMENT if procurement else (
            TripKind.CITY if data.get("kind") == TripKind.CITY else TripKind.CLASSIC
        )
        if kind == TripKind.CITY and data.get("date_start") and not data.get("date_end"):
            data["date_end"] = data["date_start"]
        trip = Trip.objects.create(
            kind=kind,
            title=title,
            destination=(data.get("destination") or "").strip(),
            procurement=procurement,
            date_start=data.get("date_start"),
            date_end=data.get("date_end"),
            lead=lead,
            note=data.get("note", ""),
        )
        if data.get("member_ids"):
            trip.members.set(Employee.objects.filter(pk__in=data["member_ids"]).exclude(pk=lead.pk))
        return Response(trip_detail(trip, user), status=status.HTTP_201_CREATED)


class TripDetailView(APIView):
    def get(self, request, pk: int):
        return Response(trip_detail(require_trip(request, pk), request.user))

    def patch(self, request, pk: int):
        trip = require_trip(request, pk, write=True)
        payload = TripWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        for field in ("title", "destination", "date_start", "date_end", "status", "note"):
            if field in data:
                setattr(trip, field, data[field])
        if not trip.title.strip():
            raise ValidationError({"detail": "У командировки должно быть название"})
        if trip.date_start and trip.date_end and trip.date_end < trip.date_start:
            raise ValidationError({"detail": "Возвращение раньше выезда"})
        if "lead_id" in data and is_manager(request.user):
            trip.lead = get_object_or_404(Employee, pk=data["lead_id"])
        trip.save()
        if "member_ids" in data:
            trip.members.set(Employee.objects.filter(pk__in=data["member_ids"]).exclude(pk=trip.lead_id))
        return Response(trip_detail(trip, request.user))

    def delete(self, request, pk: int):
        trip = require_trip(request, pk, write=True)
        stops = list(trip.stops.select_related("work_order"))
        if any(stop.work_order and stop.work_order.verifications.exists() for stop in stops):
            raise ValidationError({"detail": "В командировке уже есть поверки — её можно только завершить или отменить"})
        with transaction.atomic():
            for stop in stops:
                service.remove_stop(stop)
            trip.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StopWriteSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=350, required=False, allow_blank=True)
    site_id = serializers.IntegerField(required=False, allow_null=True)
    client_name = serializers.CharField(max_length=250, required=False, allow_blank=True)
    contact_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    latitude = serializers.FloatField(required=False, allow_null=True, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, allow_null=True, min_value=-180, max_value=180)
    postal_code = serializers.CharField(max_length=6, required=False, allow_blank=True)
    fias_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    district = serializers.CharField(max_length=120, required=False, allow_blank=True)
    planned_date = serializers.DateField(required=False, allow_null=True)
    planned_count = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=9999)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True)
    is_restricted = serializers.BooleanField(required=False)
    access_note = serializers.CharField(required=False, allow_blank=True)
    added_on_site = serializers.BooleanField(required=False)
    status = serializers.ChoiceField(choices=StopStatus.choices, required=False)
    window_start = serializers.TimeField(required=False, allow_null=True)
    window_end = serializers.TimeField(required=False, allow_null=True)
    service_minutes = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=600)


def _stop_response(stop: TripStop, code: int = 200) -> Response:
    fresh = service.stops_with_counts(stop.trip).get(pk=stop.pk)
    return Response(stop_data(fresh), status=code)


class TripStopsView(APIView):
    def post(self, request, pk: int):
        trip = require_trip(request, pk, write=True)
        payload = StopWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = dict(payload.validated_data)
        data.pop("status", None)
        timing = {key: data.pop(key) for key in ("window_start", "window_end", "service_minutes") if key in data}
        if data.get("site_id") and not Site.objects.filter(pk=data["site_id"]).exists():
            raise ValidationError({"detail": "Объект не найден"})
        # Кто завёл адрес на месте — тот и поверяет; плановые адреса достаются старшему.
        me = employee_of(request.user)
        verifier = me if data.get("added_on_site") and me else None
        stop = guarded(lambda: service.add_stop(trip, service.StopInput(**data), employee=verifier))
        if timing:
            for key, value in timing.items():
                setattr(stop, key, value)
            stop.save(update_fields=list(timing))
        return _stop_response(stop, status.HTTP_201_CREATED)


class TripStopsBulkView(APIView):
    def post(self, request, pk: int):
        trip = require_trip(request, pk, write=True)
        items = service.parse_lines(str(request.data.get("text") or ""))
        if not items:
            raise ValidationError({"detail": "Не нашёл ни одного адреса — по одному в строке"})
        if len(items) > 300:
            raise ValidationError({"detail": "За раз — не больше 300 адресов"})
        added, skipped = 0, []
        for item in items:
            try:
                service.add_stop(trip, item)
                added += 1
            except service.TripError as exc:
                skipped.append(str(exc))
        return Response({"added": added, "skipped": skipped, "trip": trip_detail(trip, request.user)})


class TripStopsReorderView(APIView):
    def post(self, request, pk: int):
        trip = require_trip(request, pk, write=True)
        ids = request.data.get("order")
        if not isinstance(ids, list) or not all(isinstance(item, int) for item in ids):
            raise ValidationError({"detail": "order — список идентификаторов адресов"})
        service.reorder(trip, ids)
        return Response(trip_detail(trip, request.user))


class TripLocateView(APIView):
    def post(self, request, pk: int):
        trip = require_trip(request, pk, write=True)
        if not geocode.is_enabled():
            raise ValidationError({"detail": "Поиск координат выключен в настройках"})
        result = service.locate_missing(trip, limit=15)
        result["trip"] = trip_detail(trip, request.user)
        return Response(result)


class TripNextVisitView(APIView):
    def post(self, request, pk: int):
        source = require_trip(request, pk, write=True)
        payload = TripWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        trip = service.next_visit(
            source, title=data.get("title", ""), date_start=data.get("date_start"), date_end=data.get("date_end"),
        )
        return Response(trip_detail(trip, request.user), status=status.HTTP_201_CREATED)


class PlanSerializer(serializers.Serializer):
    start = serializers.ChoiceField(choices=["base", "me", "first"], default="base")
    latitude = serializers.FloatField(required=False, allow_null=True, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, allow_null=True, min_value=-180, max_value=180)
    departure = serializers.DateTimeField(required=False, allow_null=True)
    service_minutes = serializers.IntegerField(default=20, min_value=1, max_value=600)
    per_instrument_minutes = serializers.IntegerField(default=5, min_value=0, max_value=120)
    day_start = serializers.TimeField(required=False)
    day_end = serializers.TimeField(required=False)
    return_to_start = serializers.BooleanField(default=False)
    multi_day = serializers.BooleanField(required=False)
    apply = serializers.BooleanField(default=False)


class TripPlanView(APIView):
    """POST /api/trips/{id}/plan/ — порядок объезда с учётом пробок и окон; apply=true — сохранить."""

    def post(self, request, pk: int):
        trip = require_trip(request, pk, write=True)
        payload = PlanSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        me = (data["latitude"], data["longitude"]) if data.get("latitude") is not None and data.get("longitude") is not None else None
        start, end = planner.work_day()
        # Выезд на несколько дней — раскладываем адреса по рабочим дням до даты возвращения.
        spans = bool(trip.date_start and trip.date_end and trip.date_end > trip.date_start)
        options = planner.PlanOptions(
            start=data["start"], me=me, departure=data.get("departure"),
            service_minutes=data["service_minutes"], per_instrument_minutes=data["per_instrument_minutes"],
            day_start=data.get("day_start") or start, day_end=data.get("day_end") or end,
            return_to_start=data["return_to_start"],
            last_day=trip.date_end if data.get("multi_day", spans) and trip.date_end else None,
        )
        try:
            result = planner.plan(trip, options, apply=data["apply"])
        except planner.PlanError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        if data["apply"]:
            result["trip"] = trip_detail(trip, request.user)
        return Response(result)


class TripStopDetailView(APIView):
    def _stop(self, request, pk: int) -> TripStop:
        stop = get_object_or_404(TripStop.objects.select_related("trip", "site", "work_order"), pk=pk)
        require_trip(request, stop.trip_id, write=True)
        return stop

    def patch(self, request, pk: int):
        stop = self._stop(request, pk)
        payload = StopWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        changed = []
        for field in (
            "contact_name", "contact_phone", "planned_date", "planned_count", "note",
            "window_start", "window_end", "service_minutes",
        ):
            if field in data:
                setattr(stop, field, data[field])
                changed.append(field)
        if changed:
            stop.save(update_fields=changed)

        site, site_changed = stop.site, []
        if "latitude" in data and "longitude" in data:
            site.latitude = service._coord(data["latitude"])
            site.longitude = service._coord(data["longitude"])
            site_changed += ["latitude", "longitude"]
        for field in ("is_restricted", "access_note"):
            if field in data:
                setattr(site, field, data[field])
                site_changed.append(field)
        if data.get("address") and data["address"].strip() != site.address:
            site.address = " ".join(data["address"].split())
            site_changed.append("address")
        if site_changed:
            site.save(update_fields=site_changed)

        if "status" in data:
            guarded(lambda: service.set_stop_status(stop, data["status"]))
        return _stop_response(stop)

    def delete(self, request, pk: int):
        stop = self._stop(request, pk)
        guarded(lambda: service.remove_stop(stop))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Закупки
# ---------------------------------------------------------------------------
class ProcurementItemSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=250)
    quantity = serializers.IntegerField(min_value=1, max_value=100000)


class ProcurementWriteSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=40, required=False, allow_blank=True)
    law = serializers.ChoiceField(choices=ProcurementLaw.choices, required=False)
    title = serializers.CharField(max_length=300)
    customer_id = serializers.IntegerField(required=False, allow_null=True)
    customer_name = serializers.CharField(max_length=250, required=False, allow_blank=True)
    customer_inn = serializers.CharField(max_length=12, required=False, allow_blank=True)
    contract_number = serializers.CharField(max_length=80, required=False, allow_blank=True)
    contract_date = serializers.DateField(required=False, allow_null=True)
    date_start = serializers.DateField(required=False, allow_null=True)
    deadline = serializers.DateField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=Decimal("0"))
    eis_url = serializers.URLField(max_length=400, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=ProcurementStatus.choices, required=False)
    responsible_id = serializers.IntegerField(required=False, allow_null=True)
    contact = serializers.CharField(max_length=250, required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    items = ProcurementItemSerializer(many=True, required=False)

    def validate_customer_inn(self, value: str) -> str:
        value = value.strip()
        if value and (not value.isdigit() or len(value) not in (10, 12)):
            raise serializers.ValidationError("ИНН — 10 или 12 цифр")
        return value


def _customer(data: dict, current: Client | None = None) -> Client | None:
    if data.get("customer_id"):
        return get_object_or_404(Client, pk=data["customer_id"])
    name = (data.get("customer_name") or "").strip()
    inn = data.get("customer_inn") or ""
    if not name:
        return current
    if current is not None and current.name == name:
        if inn and current.inn != inn:
            current.inn = inn
            current.save(update_fields=["inn"])
        return current
    found = (Client.objects.filter(inn=inn).first() if inn else None) or Client.objects.filter(
        kind=ClientKind.ORG, name__iexact=name
    ).first()
    return found or Client.objects.create(kind=ClientKind.ORG, name=name, inn=inn)


def _apply_procurement(item: Procurement, data: dict) -> None:
    for field in (
        "number", "law", "title", "contract_number", "contract_date", "date_start", "deadline",
        "amount", "eis_url", "status", "contact", "note",
    ):
        if field in data:
            value = data[field]
            setattr(item, field, value.strip() if isinstance(value, str) else value)
    if "responsible_id" in data:
        item.responsible = (
            get_object_or_404(Employee, pk=data["responsible_id"]) if data["responsible_id"] else None
        )


def _set_items(item: Procurement, rows: list[dict]) -> None:
    item.items.all().delete()
    ProcurementItem.objects.bulk_create(
        ProcurementItem(procurement=item, title=row["title"].strip(), quantity=row["quantity"], position=index)
        for index, row in enumerate(rows)
    )


class ProcurementListView(APIView):
    def get(self, request):
        qs = visible_procurements(request.user).annotate(trips_total=Count("trips", distinct=True))
        state = request.query_params.get("state")
        if state == "open":
            qs = qs.filter(status__in=[ProcurementStatus.PREPARING, ProcurementStatus.ACTIVE])
        elif state == "closed":
            qs = qs.filter(status__in=[ProcurementStatus.DONE, ProcurementStatus.CANCELLED])
        result = []
        for item in qs[:200]:
            row = procurement_brief(item)
            row["trips_total"] = item.trips_total
            row["progress"] = service.procurement_progress(item)
            result.append(row)
        return Response(result)

    def post(self, request):
        require_manager(request)
        payload = ProcurementWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        customer = _customer(data)
        if customer is None:
            raise ValidationError({"detail": "Укажите заказчика"})
        if not (data.get("number") or data.get("contract_number")):
            raise ValidationError({"detail": "Нужен номер закупки или номер контракта"})
        with transaction.atomic():
            item = Procurement(customer=customer)
            _apply_procurement(item, data)
            if "responsible_id" not in data:
                item.responsible = employee_of(request.user)
            item.save()
            _set_items(item, data.get("items", []))
        return Response(procurement_detail(item, request.user), status=status.HTTP_201_CREATED)


class ProcurementDetailView(APIView):
    def get(self, request, pk: int):
        item = get_object_or_404(visible_procurements(request.user), pk=pk)
        return Response(procurement_detail(item, request.user))

    def patch(self, request, pk: int):
        require_manager(request)
        item = get_object_or_404(Procurement, pk=pk)
        payload = ProcurementWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        with transaction.atomic():
            _apply_procurement(item, data)
            item.customer = _customer(data, item.customer) or item.customer
            item.save()
            if "items" in data:
                _set_items(item, data["items"])
        return Response(procurement_detail(item, request.user))


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    kind = serializers.ChoiceField(choices=DocumentKind.choices, required=False, default=DocumentKind.OTHER)
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)


MAX_DOCUMENT_BYTES = 40 * 1024 * 1024


class ProcurementDocumentsView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk: int):
        require_manager(request)
        item = get_object_or_404(Procurement, pk=pk)
        payload = DocumentUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        upload = payload.validated_data["file"]
        if upload.size > MAX_DOCUMENT_BYTES:
            raise ValidationError({"detail": "Файл больше 40 МБ"})
        title = (payload.validated_data.get("title") or "").strip() or upload.name.rsplit(".", 1)[0]
        doc = ProcurementDocument.objects.create(
            procurement=item,
            kind=payload.validated_data["kind"],
            title=title[:200],
            file=upload,
            uploaded_by=employee_of(request.user),
        )
        return Response(document_data(doc), status=status.HTTP_201_CREATED)


class ProcurementDocumentView(APIView):
    def get(self, request, pk: int):
        doc = get_object_or_404(ProcurementDocument, pk=pk)
        if not visible_procurements(request.user).filter(pk=doc.procurement_id).exists():
            raise Http404
        if not doc.file:
            raise Http404("Файла нет")
        return sendfile(
            request, doc.file.path, attachment=True, attachment_filename=doc.file.name.rsplit("/", 1)[-1]
        )

    def delete(self, request, pk: int):
        require_manager(request)
        doc = get_object_or_404(ProcurementDocument, pk=pk)
        doc.file.delete(save=False)
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PassWriteSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField(required=False, allow_null=True)
    vehicle = serializers.CharField(max_length=80, required=False, allow_blank=True)
    site_id = serializers.IntegerField(required=False, allow_null=True)
    number = serializers.CharField(max_length=60, required=False, allow_blank=True)
    valid_from = serializers.DateField(required=False, allow_null=True)
    valid_to = serializers.DateField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=PassStatus.choices, required=False)
    note = serializers.CharField(max_length=250, required=False, allow_blank=True)

    def validate(self, attrs):
        start, end = attrs.get("valid_from"), attrs.get("valid_to")
        if start and end and end < start:
            raise serializers.ValidationError("Срок пропуска заканчивается раньше, чем начинается")
        return attrs


def _apply_pass(item: Pass, data: dict) -> None:
    for field in ("vehicle", "number", "valid_from", "valid_to", "status", "note"):
        if field in data:
            setattr(item, field, data[field])
    if "employee_id" in data:
        item.employee = get_object_or_404(Employee, pk=data["employee_id"]) if data["employee_id"] else None
    if "site_id" in data:
        item.site = get_object_or_404(Site, pk=data["site_id"]) if data["site_id"] else None
    if not item.employee_id and not item.vehicle.strip():
        raise ValidationError({"detail": "Пропуск — на сотрудника или на автомобиль"})


class ProcurementPassesView(APIView):
    parser_classes = [JSONParser]

    def post(self, request, pk: int):
        require_manager(request)
        procurement = get_object_or_404(Procurement, pk=pk)
        payload = PassWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = Pass(procurement=procurement)
        _apply_pass(item, payload.validated_data)
        item.save()
        return Response(pass_data(item), status=status.HTTP_201_CREATED)


class PassDetailView(APIView):
    def patch(self, request, pk: int):
        require_manager(request)
        item = get_object_or_404(Pass, pk=pk)
        payload = PassWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        _apply_pass(item, payload.validated_data)
        item.save()
        return Response(pass_data(item))

    def delete(self, request, pk: int):
        require_manager(request)
        get_object_or_404(Pass, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
