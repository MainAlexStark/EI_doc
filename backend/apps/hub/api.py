"""API заявок: подсказка адреса, приём с сайта, диспетчерская очередь.

Публичные вьюхи (форма на сайте) — AddressSuggestView и RequestCreateView,
остальное требует авторизации, как весь остальной API.
"""

from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.core.models import Employee
from apps.hub import routing
from apps.hub.address import AddressSuggestUnavailable, DaDataClient
from apps.hub.models import Request, RequestStatus
from apps.verification.models import Client, ClientKind, Site, WorkOrder


class AddressSuggestThrottle(AnonRateThrottle):
    """Подсказка дёргается на каждую пару символов — обычный анонимный лимит

    (20/час, на всю форму целиком) её убил бы за пару слов. Отдельная шкала
    держит анти-спам заявки нетронутым.
    """

    scope = "address_suggest"


# ---------------------------------------------------------------------------
# Подсказка адреса
# ---------------------------------------------------------------------------
class AddressSuggestionSerializer(serializers.Serializer):
    value = serializers.CharField()
    postal_code = serializers.CharField(allow_blank=True)
    fias_id = serializers.CharField(allow_blank=True)
    district = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    latitude = serializers.FloatField(allow_null=True)
    longitude = serializers.FloatField(allow_null=True)


class AddressSuggestView(APIView):
    """GET /api/hub/address-suggest/?q=

    Без настроенного DADATA_API_KEY просто отвечает configured=false — форма
    на сайте в этом случае показывает обычное текстовое поле адреса.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AddressSuggestThrottle]

    @extend_schema(
        parameters=[OpenApiParameter("q", str)],
        responses={200: AddressSuggestionSerializer(many=True)},
    )
    def get(self, request):
        client = DaDataClient()
        if not client.is_configured:
            return Response({"configured": False, "results": []})

        query = (request.query_params.get("q") or "").strip()
        try:
            suggestions = client.suggest(query)
        except AddressSuggestUnavailable:
            return Response({"configured": True, "results": [], "unavailable": True})

        results = [
            {
                "value": item.value,
                "postal_code": item.postal_code,
                "fias_id": item.fias_id,
                "district": item.district,
                "city": item.city,
                "latitude": item.latitude,
                "longitude": item.longitude,
            }
            for item in suggestions
        ]
        return Response({"configured": True, "results": results})


# ---------------------------------------------------------------------------
# Приём заявки с сайта
# ---------------------------------------------------------------------------
class RequestCreateSerializer(serializers.Serializer):
    contact_name = serializers.CharField(max_length=200)
    contact_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)

    address = serializers.CharField(max_length=350)
    postal_code = serializers.CharField(max_length=6, required=False, allow_blank=True)
    fias_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    district = serializers.CharField(required=False, allow_blank=True, help_text="Из ответа подсказки")
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    is_address_confirmed = serializers.BooleanField(default=False)

    si_description = serializers.CharField(required=False, allow_blank=True)
    desired_date = serializers.DateField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True)

    # Honeypot: обычному человеку это поле не видно и незачем заполнять.
    # Настоящей капчи (провайдер не выбран) заменяет на первое время.
    website = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        if not (data.get("contact_phone") or data.get("contact_email")):
            raise serializers.ValidationError("Укажите телефон или email для связи")
        return data


class RequestCreateView(APIView):
    """POST /api/hub/requests/ — публичная форма заявки на сайте."""

    permission_classes = [AllowAny]  # троттлинг — DEFAULT_THROTTLE_RATES.anon = 20/час

    @extend_schema(request=RequestCreateSerializer, responses={201: None})
    def post(self, request):
        payload = RequestCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        honeypot_tripped = bool(data.pop("website", ""))
        district_name = data.pop("district", "")

        obj = Request.objects.create(
            source="site",
            ip_address=request.META.get("REMOTE_ADDR"),
            honeypot_tripped=honeypot_tripped,
            status=RequestStatus.SPAM if honeypot_tripped else RequestStatus.NEW,
            district=None if honeypot_tripped else routing.resolve_district(district_name),
            **data,
        )
        if not honeypot_tripped:
            routing.route(obj)

        return Response({"id": obj.id, "status": obj.status}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Диспетчерская очередь
# ---------------------------------------------------------------------------
class RequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    source = serializers.CharField()
    status = serializers.CharField()
    status_display = serializers.CharField(source="get_status_display")
    contact_name = serializers.CharField()
    contact_phone = serializers.CharField()
    contact_email = serializers.CharField()
    address = serializers.CharField()
    district = serializers.CharField(source="district.name", default="", allow_null=True)
    si_description = serializers.CharField()
    desired_date = serializers.DateField(allow_null=True)
    comment = serializers.CharField()
    suggested_employee = serializers.CharField(source="suggested_employee.full_name", default="", allow_null=True)
    suggested_employee_id = serializers.IntegerField(allow_null=True)
    assigned_employee = serializers.CharField(source="assigned_employee.full_name", default="", allow_null=True)
    assigned_employee_id = serializers.IntegerField(allow_null=True)
    reject_reason = serializers.CharField()
    is_address_confirmed = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class RequestListView(APIView):
    """GET /api/hub/requests/?status=&district=&assigned_employee="""

    def get(self, request):
        qs = Request.objects.select_related("district", "suggested_employee", "assigned_employee")
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        district_param = request.query_params.get("district")
        if district_param:
            qs = qs.filter(district_id=district_param)
        assigned_param = request.query_params.get("assigned_employee")
        if assigned_param:
            qs = qs.filter(assigned_employee_id=assigned_param)
        return Response(RequestSerializer(qs[:200], many=True).data)


class RequestRouteView(APIView):
    """POST /api/hub/requests/{id}/route/ — пересчитать рекомендацию вручную."""

    def post(self, request, pk: int):
        obj = get_object_or_404(Request, pk=pk)
        routing.route(obj)
        return Response(RequestSerializer(obj).data)


class RequestConfirmSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    scheduled_date = serializers.DateField(required=False, allow_null=True)
    client_id = serializers.IntegerField(required=False, allow_null=True)
    site_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)


class RequestConfirmView(APIView):
    """POST /api/hub/requests/{id}/confirm/ — превращает заявку в наряд.

    Клиента и объект берёт по переданным id, если диспетчер нашёл дубль,
    иначе заводит новые из данных заявки — так подтверждение не блокируется
    поиском совпадений, но и не плодит дубли, когда они уже найдены.
    """

    @extend_schema(request=RequestConfirmSerializer, responses={201: None})
    def post(self, request, pk: int):
        obj = get_object_or_404(Request, pk=pk)
        if obj.status == RequestStatus.CONFIRMED:
            return Response({"detail": "Заявка уже подтверждена"}, status=status.HTTP_400_BAD_REQUEST)

        payload = RequestConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        employee = get_object_or_404(Employee, pk=data["employee_id"])

        with transaction.atomic():
            if data.get("client_id"):
                client = get_object_or_404(Client, pk=data["client_id"])
            else:
                client = Client.objects.create(
                    kind=ClientKind.PERSON,
                    name=obj.contact_name,
                    phone=obj.contact_phone,
                    email=obj.contact_email,
                )

            if data.get("site_id"):
                site = get_object_or_404(Site, pk=data["site_id"])
            else:
                site = Site.objects.create(
                    client=client,
                    address=obj.address,
                    postal_code=obj.postal_code,
                    fias_id=obj.fias_id,
                    district=obj.district,
                    latitude=obj.latitude,
                    longitude=obj.longitude,
                )

            work_order = WorkOrder.objects.create(
                request=obj,
                client=client,
                site=site,
                assigned_employee=employee,
                scheduled_date=data.get("scheduled_date") or obj.desired_date,
                note=data.get("note", ""),
            )

            obj.mark_confirmed(employee=employee)

        _notify_work_order_assigned(work_order)
        return Response({"work_order_id": work_order.id}, status=status.HTTP_201_CREATED)


class RequestRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=250, required=False, allow_blank=True)
    spam = serializers.BooleanField(default=False)


class RequestRejectView(APIView):
    """POST /api/hub/requests/{id}/reject/"""

    @extend_schema(request=RequestRejectSerializer, responses={200: None})
    def post(self, request, pk: int):
        obj = get_object_or_404(Request, pk=pk)
        payload = RequestRejectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        obj.status = RequestStatus.SPAM if payload.validated_data["spam"] else RequestStatus.REJECTED
        obj.reject_reason = payload.validated_data.get("reason", "")
        obj.save(update_fields=["status", "reject_reason"])
        return Response(RequestSerializer(obj).data)


def _notify_work_order_assigned(work_order: WorkOrder) -> None:
    from apps.core.telegram import send_message

    employee = work_order.assigned_employee
    if not employee.telegram_chat_id:
        return
    when = work_order.scheduled_date.strftime("%d.%m.%Y") if work_order.scheduled_date else "дата не указана"
    send_message(
        employee.telegram_chat_id,
        f"Новый наряд №{work_order.id}: {work_order.site.address}, {when}",
    )
