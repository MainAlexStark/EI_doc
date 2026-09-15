"""Полевая работа поверителя: заводит поверку прямо на объекте, часто без связи.

Отдельный файл от api_workorders.py (диспетчер работает с нарядом целиком) и
от api.py (ввод измерений в уже существующую поверку) — здесь узкий шаг между
ними: выбрать наряд и завести СИ, за которое берёшься.

Идемпотентность по client_id (см. docstring Verification.client_id): повторная
отправка той же операции с устройства — после разрыва связи или просто ретрая
из офлайн-очереди — не плодит поверки. Экземпляр СИ ищется/заводится по паре
(тип, зав. номер) — свой UniqueConstraint уже не даст завести дубль, даже если
клиент по ошибке отправит один и тот же прибор с разными client_id.

Методику и эталоны здесь сознательно не спрашиваем: на объекте, часто без
связи и без под рукой каталога эталонов, это лишний повод не отправить
поверку. Нормоконтроль дополняет их позже через админку.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import conditions as wm_conditions
from apps.catalog.models import SiType
from apps.verification.models import Instrument, Verification, VerificationStatus, WorkOrder


class FieldVerificationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    client_id = serializers.UUIDField()
    work_order = serializers.IntegerField(source="work_order_id")
    si_type_id = serializers.IntegerField(source="instrument.si_type_id")
    instrument = serializers.CharField(source="instrument.si_type.name")
    serial_number = serializers.CharField(source="instrument.serial_number")
    verified_at = serializers.DateTimeField()
    status = serializers.CharField()
    status_display = serializers.CharField(source="get_status_display")
    suitable = serializers.BooleanField()
    needs_review = serializers.BooleanField()


class FieldVerificationCreateSerializer(serializers.Serializer):
    """То, что реально успеваешь ввести на месте — остальное не должно
    держать человека на объекте."""

    client_id = serializers.UUIDField(
        help_text="Заводится на устройстве один раз и переживает переотправку из офлайн-очереди"
    )
    si_type_id = serializers.IntegerField()
    serial_number = serializers.CharField(max_length=60)
    manufacture_year = serializers.IntegerField(required=False, allow_null=True)
    verified_at = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        attrs.setdefault("verified_at", timezone.now())
        return attrs


class WorkOrderVerificationsView(APIView):
    """GET/POST /api/work-orders/{id}/verifications/

    GET — что уже заведено по наряду (не завести один прибор дважды).
    POST — завести поверку; идемпотентно по client_id.
    """

    @extend_schema(responses={200: FieldVerificationSerializer(many=True)})
    def get(self, request, pk: int):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        qs = work_order.verifications.select_related("instrument__si_type").order_by("-created_at")
        return Response(FieldVerificationSerializer(qs, many=True).data)

    @extend_schema(request=FieldVerificationCreateSerializer, responses={201: FieldVerificationSerializer})
    def post(self, request, pk: int):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        employee = getattr(request.user, "employee", None)
        if employee is None:
            raise PermissionDenied("У учётной записи нет привязанного сотрудника")

        payload = FieldVerificationCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        existing = Verification.objects.filter(client_id=data["client_id"]).first()
        if existing is not None:
            # Повтор той же отправки (например, после разрыва связи на ответе) —
            # ничего не создаём заново, просто отдаём то, что уже есть.
            return Response(FieldVerificationSerializer(existing).data, status=status.HTTP_200_OK)

        si_type = get_object_or_404(SiType, pk=data["si_type_id"])

        try:
            with transaction.atomic():
                instrument, _ = Instrument.objects.get_or_create(
                    si_type=si_type,
                    serial_number=data["serial_number"],
                    defaults={
                        "manufacture_year": data.get("manufacture_year"),
                        "owner": work_order.client,
                        "site": work_order.site,
                    },
                )
                ambient = wm_conditions.ambient_for(data["verified_at"].date(), family=si_type.family)
                verification = Verification.objects.create(
                    client_id=data["client_id"],
                    work_order=work_order,
                    instrument=instrument,
                    verifier=employee,
                    verified_at=data["verified_at"],
                    status=VerificationStatus.DRAFT,
                    **ambient.as_fields(),
                )
        except IntegrityError:
            # Гонка двух отправок одного и того же client_id подряд — редко,
            # но офлайн-очередь может успеть повторить запрос до первого ответа.
            verification = get_object_or_404(Verification, client_id=data["client_id"])
            return Response(FieldVerificationSerializer(verification).data, status=status.HTTP_200_OK)

        return Response(FieldVerificationSerializer(verification).data, status=status.HTTP_201_CREATED)
