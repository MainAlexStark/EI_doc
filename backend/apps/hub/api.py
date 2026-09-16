"""API заявок: подсказка адреса, приём с сайта, диспетчерская очередь.

Публичные вьюхи (форма на сайте) — AddressSuggestView и RequestCreateView,
остальное требует авторизации, как весь остальной API.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.catalog.models import MeasurementFamily
from apps.core.models import Employee
from apps.hub import captcha, routing
from apps.hub.address import AddressSuggestUnavailable, DaDataClient
from apps.hub.models import Request, RequestItem, RequestStatus
from apps.hub.pricing import estimate
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
# Конфиг капчи (Yandex SmartCaptcha) — публичный ключ для виджета
# ---------------------------------------------------------------------------
class CaptchaConfigSerializer(serializers.Serializer):
    configured = serializers.BooleanField()
    client_key = serializers.CharField(allow_blank=True)


class CaptchaConfigView(APIView):
    """GET /api/hub/captcha-config/ — без CAPTCHA_SERVER_KEY/CAPTCHA_CLIENT_KEY

    отвечает configured=false, форма в этом случае не рисует виджет и
    полагается только на honeypot, как раньше (см. apps.hub.captcha).
    """

    permission_classes = [AllowAny]
    throttle_classes = []  # статичный конфиг, не заявка — нет смысла делить бюджет с анти-спамом

    @extend_schema(responses={200: CaptchaConfigSerializer})
    def get(self, request):
        return Response({"configured": captcha.is_configured(), "client_key": captcha.client_key()})


# ---------------------------------------------------------------------------
# Приём заявки с сайта
# ---------------------------------------------------------------------------
class RequestItemInputSerializer(serializers.Serializer):
    family_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, max_value=99)


# Формат ровно тот, что собирает маска на фронте (frontend/src/phone.ts) —
# так один и тот же паттерн проверяется на обеих сторонах.
PHONE_RE = re.compile(r"^\+7 \(\d{3}\) \d{3}-\d{2}-\d{2}$")


class RequestCreateSerializer(serializers.Serializer):
    contact_name = serializers.CharField(max_length=200)
    contact_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)

    def validate_contact_phone(self, value: str) -> str:
        if value and not PHONE_RE.fullmatch(value):
            raise serializers.ValidationError(
                "Телефон должен быть в формате +7 (900) 123-45-67"
            )
        return value

    address = serializers.CharField(max_length=350)
    postal_code = serializers.CharField(max_length=6, required=False, allow_blank=True)
    fias_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    district = serializers.CharField(required=False, allow_blank=True, help_text="Из ответа подсказки")
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    is_address_confirmed = serializers.BooleanField(default=False)

    items = RequestItemInputSerializer(many=True, required=False, default=list)
    si_description = serializers.CharField(required=False, allow_blank=True)
    desired_date = serializers.DateField(required=False, allow_null=True)
    desired_time = serializers.TimeField(required=False, allow_null=True)
    is_priority_slot = serializers.BooleanField(default=False)
    comment = serializers.CharField(required=False, allow_blank=True)

    # Согласие на обработку персональных данных — обязательный чекбокс на
    # форме (ст. 9 152-ФЗ «О персональных данных»); без него заявку не
    # принять, поэтому это не default=False, а обязательное поле, которое
    # ещё и проверяется на истинность в validate() ниже — не только на
    # присутствие в теле запроса.
    consent_given = serializers.BooleanField()

    # Honeypot: обычному человеку это поле не видно и незачем заполнять.
    website = serializers.CharField(required=False, allow_blank=True, default="")
    # Токен виджета Yandex SmartCaptcha — пусто, если капча не настроена
    # (apps.hub.captcha.is_configured()) или заявитель без JS.
    captcha_token = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_consent_given(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "Нужно согласие на обработку персональных данных, чтобы принять заявку"
            )
        return value

    def validate(self, data):
        if not (data.get("contact_phone") or data.get("contact_email")):
            raise serializers.ValidationError("Укажите телефон или email для связи")
        if not data.get("items") and not data.get("si_description", "").strip():
            raise serializers.ValidationError("Укажите хотя бы один прибор или опишите, что нужно поверить")
        return data


class RequestCreateView(APIView):
    """POST /api/hub/requests/ — публичная форма заявки на сайте.

    Цена — серверный снимок: доверять присланной клиентом сумме нельзя,
    поэтому здесь она пересчитывается заново по актуальным MeasurementFamily.price
    и PricingSettings, и это и есть то, что уходит в Request.estimated_price
    (apps.hub.pricing.estimate). Клиентский расчёт на форме — только чтобы
    цена обновлялась вживую без похода на сервер при каждом клике.
    """

    permission_classes = [AllowAny]  # троттлинг — DEFAULT_THROTTLE_RATES.anon = 20/час

    @extend_schema(request=RequestCreateSerializer, responses={201: None})
    def post(self, request):
        payload = RequestCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        honeypot_tripped = bool(data.pop("website", ""))
        captcha_token = data.pop("captcha_token", "")
        district_name = data.pop("district", "")
        items_input = data.pop("items", [])
        is_priority_slot = data.pop("is_priority_slot", False)

        # Пойманного honeypot'ом бота капчей не перепроверяем — он и так уже
        # помечен спамом ниже и сохраняется для статистики, как и раньше.
        if not honeypot_tripped and captcha.is_configured():
            if not captcha.verify(captcha_token, remote_ip=request.META.get("REMOTE_ADDR", "")):
                return Response(
                    {"detail": "Капча не пройдена — обновите её на форме и попробуйте снова"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        families_by_id = {}
        if items_input:
            families_by_id = {
                f.id: f for f in MeasurementFamily.objects.filter(
                    id__in=[item["family_id"] for item in items_input]
                )
            }
            missing = [item["family_id"] for item in items_input if item["family_id"] not in families_by_id]
            if missing:
                raise serializers.ValidationError({"items": f"Неизвестный тип прибора: {missing}"})

        needs_time = any(families_by_id[item["family_id"]].requires_time_slot for item in items_input)
        if not needs_time:
            data["desired_time"] = None
        effective_priority = is_priority_slot and needs_time

        price = estimate(
            [(families_by_id[item["family_id"]], item["quantity"]) for item in items_input],
            is_priority_slot=effective_priority,
        )

        obj = Request.objects.create(
            source="site",
            ip_address=request.META.get("REMOTE_ADDR"),
            honeypot_tripped=honeypot_tripped,
            status=RequestStatus.SPAM if honeypot_tripped else RequestStatus.NEW,
            district=None if honeypot_tripped else routing.resolve_district(district_name),
            is_priority_slot=effective_priority,
            estimated_price=price["total"] if items_input else None,
            discount_percent=price["discount_percent"] if items_input else 0,
            **data,
        )
        for item in items_input:
            family = families_by_id[item["family_id"]]
            RequestItem.objects.create(
                request=obj, family=family, quantity=item["quantity"], unit_price=family.price
            )
        if not honeypot_tripped:
            routing.route(obj)

        return Response({"id": obj.id, "status": obj.status}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Диспетчерская очередь
# ---------------------------------------------------------------------------
class RequestItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    family = serializers.CharField(source="family.name")
    family_id = serializers.IntegerField()
    quantity = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=9, decimal_places=2)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2)


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
    items = RequestItemSerializer(many=True)
    si_description = serializers.CharField()
    desired_date = serializers.DateField(allow_null=True)
    desired_time = serializers.TimeField(allow_null=True)
    is_priority_slot = serializers.BooleanField()
    estimated_price = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    discount_percent = serializers.DecimalField(max_digits=4, decimal_places=1)
    comment = serializers.CharField()
    suggested_employee = serializers.CharField(source="suggested_employee.full_name", default="", allow_null=True)
    suggested_employee_id = serializers.IntegerField(allow_null=True)
    assigned_employee = serializers.CharField(source="assigned_employee.full_name", default="", allow_null=True)
    assigned_employee_id = serializers.IntegerField(allow_null=True)
    reject_reason = serializers.CharField()
    is_address_confirmed = serializers.BooleanField()
    consent_given = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class RequestListView(APIView):
    """GET /api/hub/requests/?status=&district=&assigned_employee="""

    def get(self, request):
        qs = Request.objects.select_related(
            "district", "suggested_employee", "assigned_employee"
        ).prefetch_related("items__family")
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
    scheduled_time = serializers.TimeField(required=False, allow_null=True)
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
                scheduled_time=data.get("scheduled_time") or obj.desired_time,
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
    if work_order.scheduled_time:
        when += f", {work_order.scheduled_time:%H:%M}"
    send_message(
        employee.telegram_chat_id,
        f"Новый наряд №{work_order.id}: {work_order.site.address}, {when}",
    )
