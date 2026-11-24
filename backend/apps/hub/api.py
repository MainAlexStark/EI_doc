"""API заявок: подсказка адреса, приём с сайта, диспетчерская очередь.

Публичные вьюхи (форма на сайте) — AddressSuggestView и RequestCreateView,
остальное требует авторизации, как весь остальной API.
"""

from __future__ import annotations

import re

from django.db.models import CharField, F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, NullIf
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.catalog.models import MeasurementFamily
from apps.core import paging
from apps.core.models import Employee
from apps.core.net import client_ip
from apps.hub import captcha, routing, service
from apps.hub.address import AddressSuggestUnavailable, DaDataClient
from apps.hub.models import Request, RequestItem, RequestStatus
from apps.hub.pricing import estimate
from apps.verification.models import Client, Site


class PublicRequestThrottle(AnonRateThrottle):
    """Заявок с одного адреса — не больше ``public_request`` (по умолчанию 20 в час)."""

    scope = "public_request"


class PublicRequestBurstThrottle(AnonRateThrottle):
    """И не пачкой: бот, отправляющий заявки подряд, упрётся через несколько штук."""

    scope = "public_request_burst"


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

    permission_classes = [AllowAny]
    # Свой бюджет на IP (DEFAULT_THROTTLE_RATES.public_request / _burst); адрес за Caddy —
    # из X-Forwarded-For (REST_FRAMEWORK.NUM_PROXIES), иначе все посетители делили бы один лимит.
    throttle_classes = [PublicRequestBurstThrottle, PublicRequestThrottle]

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
            if not captcha.verify(captcha_token, remote_ip=client_ip(request)):
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
            ip_address=client_ip(request) or None,
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


def _can_control(user) -> bool:
    from apps.logistics.dispatch import can_control

    return can_control(user)


def _forbid_unless_control(request) -> Response | None:
    if _can_control(request.user):
        return None
    return Response({"detail": "Разбирать заявки могут руководство, метролог и менеджер"},
                    status=status.HTTP_403_FORBIDDEN)


def visible_requests(user, qs):
    """Заявки с ПДн заявителей: руководство и наблюдатель видят все, поверитель — только свои
    (назначенные или подобранные ему), остальные — ни одной."""
    from apps.core.models import Role

    if _can_control(user) or getattr(user, "role", "") == Role.OBSERVER:
        return qs
    employee = getattr(user, "employee", None)
    if employee is None:
        return qs.none()
    return qs.filter(Q(assigned_employee=employee) | Q(suggested_employee=employee))


def with_last_change(qs):
    """«Последний изменил»: время и автор последней записи simple-history — подзапросами, без N+1.

    Автор — ФИО сотрудника, иначе почта учётной записи; пусто — изменение без входа (форма на сайте)."""
    last = Request.history.model.objects.filter(id=OuterRef("pk")).order_by("-history_date", "-history_id")
    return qs.annotate(
        last_change_at=Subquery(last.values("history_date")[:1]),
        last_change_by=Subquery(last.annotate(
            who=Coalesce(NullIf(F("history_user__employee__full_name"), Value("")), F("history_user__email"),
                         output_field=CharField()),
        ).values("who")[:1], output_field=CharField()),
    )


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
    updated_at = serializers.SerializerMethodField()
    updated_by = serializers.SerializerMethodField()

    @staticmethod
    def _last(obj):
        if hasattr(obj, "last_change_at"):  # with_last_change() — списки
            return obj.last_change_at, obj.last_change_by or ""
        row = obj.history.select_related("history_user__employee").order_by("-history_date", "-history_id").first()
        if row is None:
            return None, ""
        user = row.history_user
        employee = getattr(user, "employee", None) if user else None
        return row.history_date, (employee.full_name if employee and employee.full_name else (user.email if user else ""))

    def get_updated_at(self, obj):
        at = self._last(obj)[0]
        return serializers.DateTimeField().to_representation(at) if at else None

    def get_updated_by(self, obj) -> str:
        return self._last(obj)[1]


class RequestListView(APIView):
    """GET /api/hub/requests/?status=&district=&assigned_employee=&q=

    Массив до ``limit`` (200) или страница при ``?page_size=`` — см. apps.core.paging.
    """

    def get(self, request):
        qs = with_last_change(Request.objects.select_related(
            "district", "suggested_employee", "assigned_employee"
        ).prefetch_related("items__family"))
        qs = visible_requests(request.user, qs)
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        district_param = request.query_params.get("district")
        if district_param:
            qs = qs.filter(district_id=district_param)
        assigned_param = request.query_params.get("assigned_employee")
        if assigned_param:
            qs = qs.filter(assigned_employee_id=assigned_param)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(contact_name__icontains=q) | Q(contact_phone__icontains=q) | Q(address__icontains=q)
            )
        return paging.respond(request, qs, lambda rows: RequestSerializer(rows, many=True).data)


class RequestRouteView(APIView):
    """POST /api/hub/requests/{id}/route/ — пересчитать рекомендацию вручную."""

    def post(self, request, pk: int):
        if (denied := _forbid_unless_control(request)) is not None:
            return denied
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
        if (denied := _forbid_unless_control(request)) is not None:
            return denied
        obj = get_object_or_404(Request, pk=pk)
        if obj.status == RequestStatus.CONFIRMED:
            return Response({"detail": "Заявка уже подтверждена"}, status=status.HTTP_400_BAD_REQUEST)

        payload = RequestConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        employee = get_object_or_404(Employee, pk=data["employee_id"])
        client = get_object_or_404(Client, pk=data["client_id"]) if data.get("client_id") else None
        site = get_object_or_404(Site, pk=data["site_id"]) if data.get("site_id") else None
        try:
            work_order = service.confirm_request(
                obj, employee=employee, scheduled_date=data.get("scheduled_date"),
                scheduled_time=data.get("scheduled_time"), client=client, site=site, note=data.get("note", ""),
            )
        except service.RequestActionError as exc:  # подтвердили параллельно — проверка под блокировкой
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"work_order_id": work_order.id}, status=status.HTTP_201_CREATED)


class RequestRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=250, required=False, allow_blank=True)
    spam = serializers.BooleanField(default=False)


class RequestRejectView(APIView):
    """POST /api/hub/requests/{id}/reject/"""

    @extend_schema(request=RequestRejectSerializer, responses={200: None})
    def post(self, request, pk: int):
        if (denied := _forbid_unless_control(request)) is not None:
            return denied
        obj = get_object_or_404(Request, pk=pk)
        payload = RequestRejectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        service.reject_request(
            obj, reason=payload.validated_data.get("reason", ""), spam=payload.validated_data["spam"],
        )
        return Response(RequestSerializer(obj).data)


BULK_ACTIONS = ("confirm", "assign", "reject", "spam", "reopen")
BULK_MAX = 100


class RequestBulkSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=BULK_ACTIONS)
    ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=BULK_MAX)
    employee_id = serializers.IntegerField(required=False, allow_null=True)
    scheduled_date = serializers.DateField(required=False, allow_null=True)
    scheduled_time = serializers.TimeField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=250, required=False, allow_blank=True)


class RequestBulkView(APIView):
    """POST /api/hub/requests/bulk/ — разбор входящих пачкой (режим «Заявки»).

    {"action": "confirm"|"assign"|"reject"|"spam"|"reopen", "ids": [..], "employee_id"?, "scheduled_date"?,
     "scheduled_time"?, "reason"?}

    * confirm — наряд по каждой заявке; исполнитель — employee_id, иначе подобранный роутингом;
    * assign  — только назначить исполнителя (заявка «Подобран исполнитель»), наряд не заводится;
    * reject / spam — отклонить с причиной / пометить спамом;
    * reopen  — вернуть во «Новые» («Отменить» в тосте): снять подбор, назначение и отклонение;
      наряды заявки без поверок отменяются. Нельзя, если по заявке уже есть поверки или сканы.

    Каждая заявка — своя транзакция: одна неудачная не откатывает остальные. Повтор того же
    действия безопасен — уже подтверждённая/отклонённая заявка просто попадает в ``skipped``.
    Ответ: {"done": [id], "skipped": [{"id", "reason"}], "errors": [{"id", "error"}], "items": [заявка]}.
    """

    def post(self, request):
        if not _can_control(request.user):
            return Response({"detail": "Разбирать заявки пачкой могут руководство, метролог и менеджер"},
                            status=status.HTTP_403_FORBIDDEN)
        payload = RequestBulkSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        action = data["action"]
        employee = None
        if data.get("employee_id"):
            employee = get_object_or_404(Employee, pk=data["employee_id"], is_active=True)
        if action == "assign" and employee is None:
            raise serializers.ValidationError({"employee_id": "Кому назначить?"})

        from django.db import transaction

        ids = list(dict.fromkeys(data["ids"]))
        done, skipped, errors = [], [], []
        for pk in ids:
            try:
                # Своя транзакция и блокировка строки на каждую заявку: статус проверяется под замком,
                # двойной клик или два диспетчера не подтвердят одну заявку дважды.
                with transaction.atomic():
                    obj = Request.objects.select_for_update().filter(pk=pk).first()
                    if obj is None:
                        errors.append({"id": pk, "error": "Заявки нет"})
                        continue
                    outcome = self._apply(obj, action, employee, data)
            except service.AlreadyConfirmed:
                outcome = "уже подтверждена"
            except (service.RequestActionError, ValueError) as exc:
                errors.append({"id": pk, "error": str(exc)})
                continue
            if outcome:
                skipped.append({"id": pk, "reason": outcome})
            else:
                done.append(pk)
        items = with_last_change(Request.objects.filter(pk__in=ids).select_related(
            "district", "suggested_employee", "assigned_employee"
        ).prefetch_related("items__family"))
        return Response({
            "done": done, "skipped": skipped, "errors": errors,
            "items": RequestSerializer(items, many=True).data,
        })

    @staticmethod
    def _apply(obj: Request, action: str, employee, data: dict) -> str:
        """Пусто — сделано; текст — почему пропущено (уже в нужном состоянии)."""
        from django.db import transaction

        closed = (RequestStatus.CONFIRMED, RequestStatus.DONE, RequestStatus.CANCELLED)
        if action == "confirm":
            if obj.status in closed:
                return "уже подтверждена"
            if obj.status in (RequestStatus.REJECTED, RequestStatus.SPAM):
                return "отклонена — сначала верните её"
            who = employee or obj.suggested_employee
            if who is None:
                raise ValueError("Не подобран исполнитель — укажите employee_id")
            with transaction.atomic():
                service.confirm_request(
                    obj, employee=who, scheduled_date=data.get("scheduled_date"),
                    scheduled_time=data.get("scheduled_time"),
                )
            return ""
        if action == "assign":
            if obj.status not in (RequestStatus.NEW, RequestStatus.ROUTED):
                return "заявка уже разобрана"
            obj.suggested_employee = employee
            obj.status = RequestStatus.ROUTED
            obj.routed_at = timezone.now()
            obj.save(update_fields=["suggested_employee", "status", "routed_at"])
            return ""
        if action == "reopen":
            return RequestBulkView._reopen(obj)
        spam = action == "spam"
        target = RequestStatus.SPAM if spam else RequestStatus.REJECTED
        if obj.status == target:
            return "уже отмечена"
        if obj.status in closed:
            raise ValueError("По заявке уже есть наряд — отмените наряд, а не заявку")
        service.reject_request(obj, reason=data.get("reason", ""), spam=spam)
        return ""


    @staticmethod
    def _reopen(obj: Request) -> str:
        from django.db import transaction

        from apps.verification.models import ScanUpload, Verification, WorkOrderStatus

        if obj.status == RequestStatus.NEW and not obj.suggested_employee_id and not obj.assigned_employee_id:
            return "уже во «Новых»"
        orders = obj.work_orders.all()
        if Verification.objects.filter(work_order__in=orders).exists() or ScanUpload.objects.filter(work_order__in=orders).exists():
            raise ValueError("По заявке уже есть поверки — вернуть во «Новые» нельзя")
        with transaction.atomic():
            # update(), не save(): sync_request_status() не должен перетереть возврат во «Новые».
            orders.exclude(status=WorkOrderStatus.CANCELLED).update(status=WorkOrderStatus.CANCELLED)
            obj.status = RequestStatus.NEW
            obj.suggested_employee = obj.assigned_employee = None
            obj.reject_reason = ""
            obj.routed_at = obj.confirmed_at = None
            obj.save(update_fields=["status", "suggested_employee", "assigned_employee", "reject_reason",
                                    "routed_at", "confirmed_at"])
        return ""
