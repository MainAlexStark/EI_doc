"""Ввод измерений: экран поверителя и экран сверки со сканом."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.verification import measurements as service
from apps.catalog import conditions as wm_conditions
from apps.verification.calculators import water_meter as wm
from apps.verification.models import Verification


class MeasurementRowSerializer(serializers.Serializer):
    """Одна строка. Объём по счётчику — показаниями, импульсами или напрямую."""

    seconds = serializers.IntegerField(required=False, min_value=1)
    flow_rate = serializers.CharField(help_text="Расход Q, м³/ч — режим установки")
    volume_standard = serializers.CharField(
        help_text="Vэтал, м³ — объём по поверочной установке"
    )

    reading_start = serializers.CharField(required=False, allow_blank=True)
    reading_end = serializers.CharField(required=False, allow_blank=True)
    pulses = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    volume_meter = serializers.CharField(required=False, allow_blank=True)

    confidence = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=1,
        help_text="Уверенность распознавания — только для источника scan",
    )
    needs_review = serializers.BooleanField(required=False, default=False)


class MeasurementsSerializer(serializers.Serializer):
    layout = serializers.ChoiceField(choices=sorted(wm.LAYOUTS), default=wm.LAYOUT_COMPACT)
    source = serializers.ChoiceField(choices=service.SOURCES, default=service.SOURCE_MANUAL)
    meter_class = serializers.ChoiceField(choices=[wm.CLASS_A, wm.CLASS_B], default=wm.CLASS_B)
    pulse_weight = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Коэффициент преобразования K, м³/имп — для счётчиков с импульсным выходом",
    )
    unit_type = serializers.ChoiceField(
        choices=[wm_conditions.HOT, wm_conditions.COLD], required=False, allow_blank=True,
        help_text="Тип счётчика: г/в или х/в",
    )
    water_temperature = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Температура поверочной жидкости, °С — строка в условиях поверки",
    )
    checks = serializers.DictField(
        child=serializers.BooleanField(), required=False,
        help_text=f"Отметки по пунктам: {', '.join(wm.CHECKS)}",
    )
    rows = MeasurementRowSerializer(many=True)


class ConfirmRowsSerializer(serializers.Serializer):
    rows = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)


class VerificationMeasurementsView(APIView):
    """POST /api/verifications/{id}/measurements/

    Принимает измерения, считает погрешность и выводит годность.
    Вердикт возвращается системой — отдельной «галочки годности» на входе нет.
    """

    @extend_schema(request=MeasurementsSerializer, responses={200: None})
    def post(self, request, pk: int):
        verification = get_object_or_404(
            Verification.objects.select_related("instrument__si_type"), pk=pk
        )
        payload = MeasurementsSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            applied = service.apply(verification, payload.validated_data)
        except service.MeasurementInputError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "suitable": applied.suitable,
                "rows": applied.rows,
                "failed_rows": list(applied.verdict.failed_rows),
                "reasons": list(applied.verdict.reasons),
                "journal_note": applied.journal_note,
                "needs_review": applied.needs_review,
                "status": verification.status,
            }
        )


class VerificationConfirmRowsView(APIView):
    """POST /api/verifications/{id}/measurements/confirm/

    Экран сверки: человек подтверждает распознанные строки, глядя на фото.
    Пока есть неподтверждённые, поверка остаётся черновиком.
    """

    @extend_schema(request=ConfirmRowsSerializer, responses={200: None})
    def post(self, request, pk: int):
        verification = get_object_or_404(Verification, pk=pk)
        payload = ConfirmRowsSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            remaining = service.confirm_rows(verification, payload.validated_data["rows"])
        except service.MeasurementInputError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"needs_review": remaining, "status": verification.status})


class LayoutsView(APIView):
    """GET /api/verifications/layouts/ — что показывать в форме ввода."""

    def get(self, request):
        return Response(
            {
                "layouts": {
                    name: [
                        {"mode": mode, "label": wm.MODES[mode]["label"],
                         "seconds": wm.MODES[mode]["seconds"]}
                        for mode in modes
                    ]
                    for name, modes in wm.LAYOUTS.items()
                },
                "checks": wm.CHECKS,
                "classes": [wm.CLASS_A, wm.CLASS_B],
            }
        )
