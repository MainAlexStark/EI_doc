"""Фото поверяемого СИ — по одной поверке может быть сколько угодно.

Отдельный файл от api_scan.py: там фото бланка целиком ради OCR/VLM
(``ScanUpload``), здесь — обычные рабочие снимки прибора (табличка,
повреждение, место установки) без какого-либо распознавания. Общий с
api_scan.py приём — защищённая раздача файла через django-sendfile, чтобы
фото поверки не оказались доступны без авторизации по прямой ссылке.

    GET/POST     /api/verifications/{id}/photos/        — список / загрузка
    DELETE       /api/verification-photos/{id}/         — удалить фото
    GET          /api/verification-photos/{id}/image/   — сам файл
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django_sendfile import sendfile
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.verification.models import Verification, VerificationPhoto


class VerificationPhotoSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    verification = serializers.IntegerField(source="verification_id")
    caption = serializers.CharField(allow_blank=True)
    uploaded_by = serializers.CharField(source="uploaded_by.full_name", default="", allow_null=True)
    created_at = serializers.DateTimeField()
    image_url = serializers.SerializerMethodField()

    def get_image_url(self, obj: VerificationPhoto) -> str:
        return reverse("verification_photo_image", args=[obj.pk])


class VerificationPhotoUploadSerializer(serializers.Serializer):
    photo = serializers.ImageField()
    caption = serializers.CharField(required=False, allow_blank=True, default="")


class VerificationPhotosView(APIView):
    """GET/POST /api/verifications/{id}/photos/

    GET — все фото поверки (обновление страницы не теряет список).
    POST — добавить ещё одно; поверка от этого не меняется, фото не влияют
    ни на результат, ни на протокол — чисто вспомогательный материал.
    """

    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(responses={200: VerificationPhotoSerializer(many=True)})
    def get(self, request, pk: int):
        verification = get_object_or_404(Verification, pk=pk)
        photos = verification.photos.all()
        return Response(VerificationPhotoSerializer(photos, many=True, context={"request": request}).data)

    @extend_schema(request=VerificationPhotoUploadSerializer, responses={201: VerificationPhotoSerializer})
    def post(self, request, pk: int):
        verification = get_object_or_404(Verification, pk=pk)
        employee = getattr(request.user, "employee", None)
        if employee is None:
            raise PermissionDenied("У учётной записи нет привязанного сотрудника")

        payload = VerificationPhotoUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        photo = payload.validated_data["photo"]

        obj = VerificationPhoto.objects.create(
            verification=verification,
            uploaded_by=employee,
            image=ContentFile(photo.read(), name=photo.name),
            caption=payload.validated_data.get("caption", ""),
        )
        return Response(
            VerificationPhotoSerializer(obj, context={"request": request}).data, status=status.HTTP_201_CREATED
        )


class VerificationPhotoDetailView(APIView):
    """DELETE /api/verification-photos/{id}/ — убрать неудачный/лишний снимок."""

    def delete(self, request, pk: int):
        obj = get_object_or_404(VerificationPhoto, pk=pk)
        obj.image.delete(save=False)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VerificationPhotoImageView(APIView):
    """GET /api/verification-photos/{id}/image/ — сам файл, тем же приёмом, что ScanImageView."""

    def get(self, request, pk: int):
        obj = get_object_or_404(VerificationPhoto, pk=pk)
        if not obj.image:
            raise Http404("У фото нет файла")
        return sendfile(request, obj.image.path)
