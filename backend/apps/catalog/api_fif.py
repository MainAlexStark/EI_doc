"""Подвязка к реестру ФИФ (РСТ, ФГИС «Аршин») при заведении типов СИ.

* ``GET  /api/catalog/fif/types/<ident>/`` — карточка утверждённого типа:
  модификации, изготовитель, срок свидетельства, МПИ, документы и какие
  наши типы уже заведены по этому номеру;
* ``POST /api/catalog/fif/types/<ident>/limits/`` — прочитать расходы и
  погрешности из описания типа (Yandex AI Studio, apps.arshin.limits);
* ``GET  /api/catalog/si-types/<id>/documents/`` — методика и описание типа;
* ``GET  /api/catalog/fif/documents/<id>/file/`` — сам файл (скачивается к
  себе при первом обращении, если фоновая задача ещё не успела).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django_sendfile import sendfile
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.arshin import documents as fif_documents
from apps.arshin.fif import FifClient, FifUnavailable
from apps.arshin.limits import LimitsUnavailable, extract_limits
from apps.arshin.models import FifDocument
from apps.catalog.models import SiType

logger = logging.getLogger(__name__)


def schedule_documents(mit_uuid: str) -> None:
    """Скачать документы типа в фоне. Без брокера (разработка) — просто пропускаем."""
    if not mit_uuid or not getattr(settings, "FIF_DOCUMENTS_AUTO_DOWNLOAD", True):
        return
    try:
        from apps.arshin.tasks import download_type_documents

        download_type_documents.delay(mit_uuid)
    except Exception as exc:  # noqa: BLE001 — недоступный брокер не должен ронять заведение типа
        logger.warning("Не удалось поставить скачивание документов %s: %s", mit_uuid, exc)


class FifDocumentSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    downloaded = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = FifDocument
        fields = ["id", "kind", "kind_display", "title", "url", "downloaded", "size", "error", "file_url"]

    def get_downloaded(self, obj: FifDocument) -> bool:
        return bool(obj.file)

    def get_file_url(self, obj: FifDocument) -> str:
        return f"/api/catalog/fif/documents/{obj.pk}/file/"


class FifTypeCardView(APIView):
    @extend_schema(responses={200: dict})
    def get(self, request, ident: str):
        try:
            card = FifClient().get_type(ident)
        except FifUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        docs = fif_documents.register_documents(card)
        existing = SiType.objects.filter(
            Q(fif_uuid=card.uuid) | Q(registry_number=card.registry_number), is_active=True
        ).order_by("name")
        body = card.as_dict()
        body["documents"] = FifDocumentSerializer(docs, many=True).data
        body["already_added"] = [
            {"id": t.id, "name": t.name, "needs_review": t.needs_review} for t in existing
        ]
        body["limits_reader"] = bool(
            getattr(settings, "YANDEX_VISION_API_KEY", "") and getattr(settings, "YANDEX_VISION_FOLDER_ID", "")
        )
        return Response(body)


class LimitsRequestSerializer(serializers.Serializer):
    variant = serializers.CharField(required=False, allow_blank=True, default="")


class FifTypeLimitsView(APIView):
    @extend_schema(request=LimitsRequestSerializer, responses={200: dict})
    def post(self, request, ident: str):
        payload = LimitsRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            card = FifClient().get_type(ident)
            result = extract_limits(card, payload.validated_data["variant"])
        except (FifUnavailable, LimitsUnavailable) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(result)


class SiTypeDocumentsView(APIView):
    @extend_schema(responses=FifDocumentSerializer(many=True))
    def get(self, request, pk: int):
        si_type = get_object_or_404(SiType, pk=pk)
        if not si_type.fif_uuid:
            return Response([])
        docs = FifDocument.objects.filter(mit_uuid=si_type.fif_uuid)
        if not docs.exists():
            # Тип заведён, а документы ещё не регистрировались (старый тип, фон не успел) —
            # пробуем карточку сейчас, без скачивания самих файлов.
            try:
                fif_documents.register_documents(FifClient().get_type(si_type.fif_uuid))
            except FifUnavailable:
                pass
            docs = FifDocument.objects.filter(mit_uuid=si_type.fif_uuid)
        return Response(FifDocumentSerializer(docs, many=True).data)


class FifDocumentFileView(APIView):
    def get(self, request, pk: int):
        doc = get_object_or_404(FifDocument, pk=pk)
        if not doc.file:
            doc = fif_documents.download(doc)
        if not doc.file:
            # Скачать к себе не вышло — отдаём ссылку в ФГИС, вдруг у человека она откроется.
            return Response(
                {"detail": doc.error or "Файл ещё не скачан из ФГИС", "url": doc.url},
                status=status.HTTP_404_NOT_FOUND,
            )
        return sendfile(
            request, doc.file.path, attachment=False,
            attachment_filename=doc.filename or None, mimetype=doc.content_type or None,
        )
