"""Бланк с QR и распознавание — второй сценарий офлайна (см. claude/scans.md).

Три шага, три вьюхи:

1. GET  /api/work-orders/{id}/blank/         — печатный PDF с QR и метками.
2. POST /api/work-orders/{id}/scans/         — фото → черновик распознавания
   (verification ещё не заведена — это только предложение для экрана сверки).
3. POST /api/scans/{id}/apply/               — экран сверки подтверждён
   человеком → заводим поверку (переиспользует apps.verification.api_field
   и apps.verification.measurements, как обычный ручной ввод).

GET /api/scans/{id}/image/ отдаёт исходное фото — для той же сверки.
"""

from __future__ import annotations

import logging

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django_sendfile import sendfile
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import conditions as wm_conditions
from apps.catalog.models import SiType
from apps.hub.yandex_vision import RecognitionUnavailable, YandexVisionClient
from apps.verification import measurements as service
from apps.verification import render
from apps.verification import scan as scan_service
from apps.verification.api import MeasurementsSerializer
from apps.verification.api_field import FieldVerificationSerializer
from apps.verification.models import Instrument, ScanUpload, Verification, VerificationStatus, WorkOrder

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Печатный бланк
# ---------------------------------------------------------------------------
class WorkOrderBlankView(APIView):
    """GET /api/work-orders/{id}/blank/?copies=N — распечатать бланк(и) наряда."""

    def get(self, request, pk: int):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        try:
            copies = int(request.query_params.get("copies") or 1)
        except ValueError:
            copies = 1

        if not render.typst_available():
            return Response(
                {"detail": "На сервере не установлен typst — печать бланков недоступна"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            pdf = render.render_blank(work_order, copies=copies)
        except render.RenderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = Response(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="blank-wo-{work_order.id}.pdf"'
        return response


# ---------------------------------------------------------------------------
# 2. Загрузка фото → черновик распознавания
# ---------------------------------------------------------------------------
class ScanUploadSerializer(serializers.Serializer):
    photo = serializers.ImageField()


class ScanResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    work_order = serializers.IntegerField(source="work_order_id")
    verification = serializers.IntegerField(source="verification_id", allow_null=True)
    created_at = serializers.DateTimeField()
    error = serializers.CharField(allow_blank=True)
    recognized = serializers.JSONField()
    image_url = serializers.SerializerMethodField()

    def get_image_url(self, obj: ScanUpload) -> str:
        return reverse("scan_image", args=[obj.pk])


class WorkOrderScansView(APIView):
    """GET/POST /api/work-orders/{id}/scans/

    GET — сканы наряда (в т. ч. уже подтверждённые) — чтобы не терять
    черновик распознавания при обновлении страницы.
    POST — фото бланка → черновик распознавания. Верификацию не заводит —
    это предложение для экрана сверки (см. ScanApplyView ниже). Фото
    сохраняется, даже если распознать не вышло: QR не нашёлся, метки не
    выровнялись, ключ VLM не настроен — во всех этих случаях сверка просто
    начинается с пустых полей, поверитель заполняет их вручную, глядя на то
    же фото.
    """

    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(responses={200: ScanResultSerializer(many=True)})
    def get(self, request, pk: int):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        scans = work_order.scans.order_by("-created_at")
        return Response(ScanResultSerializer(scans, many=True, context={"request": request}).data)

    @extend_schema(request=ScanUploadSerializer, responses={201: ScanResultSerializer})
    def post(self, request, pk: int):
        work_order = get_object_or_404(WorkOrder, pk=pk)
        employee = getattr(request.user, "employee", None)
        if employee is None:
            raise PermissionDenied("У учётной записи нет привязанного сотрудника")

        payload = ScanUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        photo = payload.validated_data["photo"]
        raw = photo.read()

        scan = ScanUpload.objects.create(
            work_order=work_order, uploaded_by=employee,
            image=ContentFile(raw, name=photo.name),
        )

        warning: str | None = None
        recognized: dict = {}

        try:
            image = scan_service.decode_image(raw)
        except scan_service.ScanError as exc:
            scan.error = str(exc)
            scan.save(update_fields=["error"])
            data = ScanResultSerializer(scan, context={"request": request}).data
            data["warning"] = str(exc)
            return Response(data, status=status.HTTP_201_CREATED)

        try:
            qr_data = scan_service.decode_qr(image)
        except scan_service.ScanError:
            qr_data = None
        if qr_data:
            found_wo = scan_service.work_order_id_from_qr(qr_data)
            if found_wo is not None and found_wo != work_order.id:
                warning = (
                    f"QR на фото указывает на наряд №{found_wo}, а не №{work_order.id} "
                    "— проверьте, тот ли это бланк"
                )

        try:
            aligned = scan_service.align(image)
        except scan_service.ScanError as exc:
            # Не выровнялось — распознаём как есть, это не блокирует загрузку,
            # человек всё равно всё сверит на экране.
            aligned = image
            warning = warning or str(exc)

        client = YandexVisionClient()
        if client.is_configured:
            try:
                jpeg = scan_service.encode_jpeg(aligned)
                recognized = client.recognize_blank(jpeg)
            except (scan_service.ScanError, RecognitionUnavailable) as exc:
                scan.error = str(exc)
                warning = warning or str(exc)
                logger.warning("Распознавание скана %s не удалось: %s", scan.pk, exc)
        else:
            warning = warning or "Распознавание не настроено — заполните поля вручную"

        scan.recognized = recognized
        scan.save(update_fields=["recognized", "error"])

        data = ScanResultSerializer(scan, context={"request": request}).data
        if warning:
            data["warning"] = warning
        return Response(data, status=status.HTTP_201_CREATED)


class ScanDetailView(APIView):
    """GET /api/scans/{id}/ — тот же результат распознавания (обновление страницы)."""

    def get(self, request, pk: int):
        scan = get_object_or_404(ScanUpload, pk=pk)
        return Response(ScanResultSerializer(scan, context={"request": request}).data)


class ScanImageView(APIView):
    """GET /api/scans/{id}/image/ — исходное фото для экрана сверки."""

    def get(self, request, pk: int):
        scan = get_object_or_404(ScanUpload, pk=pk)
        if not scan.image:
            raise Http404("У скана нет файла")
        return sendfile(request, scan.image.path)


# ---------------------------------------------------------------------------
# 3. Экран сверки подтверждён → заводим поверку
# ---------------------------------------------------------------------------
class ScanApplySerializer(serializers.Serializer):
    """То, что человек подтвердил на экране сверки — форма та же, что и на
    экране «Поверки», плюс данные СИ (их на бланке не было, см. models.
    ScanUpload)."""

    si_type_id = serializers.IntegerField()
    serial_number = serializers.CharField(max_length=60)
    manufacture_year = serializers.IntegerField(required=False, allow_null=True)
    verified_at = serializers.DateTimeField(required=False)
    measurements = MeasurementsSerializer()


class ScanApplyView(APIView):
    """POST /api/scans/{id}/apply/ — подтверждение сверки заводит поверку.

    Идемпотентно по scan.pk: повторный вызов для уже привязанного скана
    просто отдаёт существующую поверку, не заводя вторую — так же, как
    client_id в apps.verification.api_field, только ключ идемпотентности
    здесь другой (один скан — одна поверка, а не UUID с устройства).
    """

    @extend_schema(request=ScanApplySerializer, responses={201: FieldVerificationSerializer})
    def post(self, request, pk: int):
        scan = get_object_or_404(ScanUpload.objects.select_related("work_order"), pk=pk)
        if scan.verification_id:
            return Response(FieldVerificationSerializer(scan.verification).data, status=status.HTTP_200_OK)

        employee = getattr(request.user, "employee", None)
        if employee is None:
            raise PermissionDenied("У учётной записи нет привязанного сотрудника")

        payload = ScanApplySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        si_type = get_object_or_404(SiType, pk=data["si_type_id"])
        work_order = scan.work_order

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
            verified_at = data.get("verified_at") or timezone.now()
            ambient = wm_conditions.ambient_for(verified_at.date(), family=si_type.family)
            verification = Verification.objects.create(
                work_order=work_order,
                instrument=instrument,
                verifier=employee,
                verified_at=verified_at,
                status=VerificationStatus.DRAFT,
                **ambient.as_fields(),
            )
            scan.verification = verification
            scan.save(update_fields=["verification"])

        m_payload = dict(data["measurements"])
        m_payload["source"] = service.SOURCE_SCAN
        try:
            applied = service.apply(verification, m_payload)
        except service.MeasurementInputError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        result = FieldVerificationSerializer(verification).data
        result["rows"] = applied.rows
        return Response(result, status=status.HTTP_201_CREATED)
