"""«Перечень применяемых эталонов» — экран «Эталоны» на вкладке «Типы СИ».

Видят все сотрудники (поверителю нужно знать, что брать на выезд), правят метролог,
руководитель и владелец: руками, загрузкой нового Word-файла или через ассистента
(``ei_standard_kits_propose``).
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import standard_kits as kits
from apps.catalog.api_types import is_reviewer
from apps.catalog.models import AuxInstrument, StandardKit

MAX_DOCX = 15 * 1024 * 1024


def _check(user) -> None:
    if not is_reviewer(user):
        raise PermissionDenied("Перечень эталонов правит метролог или руководитель")


def _run(changes: list, user) -> list[str]:
    try:
        return kits.apply_changes(changes, user)
    except kits.KitError as exc:
        raise ValidationError({"detail": str(exc)}) from exc


class StandardKitsView(APIView):
    """GET — весь перечень (наборы + вспомогательные СИ), POST — добавить набор."""

    @extend_schema(responses={200: dict})
    def get(self, request):
        show_all = request.query_params.get("all") in ("1", "true")
        qs = kits.search_kits(request.query_params.get("q") or "", active_only=not show_all)
        aux = AuxInstrument.objects.all() if show_all else AuxInstrument.objects.filter(is_active=True)
        return Response({
            "kits": [kits.kit_dict(k) for k in qs],
            "aux": [kits.aux_dict(a) for a in aux],
            "can_edit": is_reviewer(request.user),
        })

    @extend_schema(request=dict, responses={201: dict})
    def post(self, request):
        _check(request.user)
        _run([{**request.data, "op": "add", "target": "kit"}], request.user)
        kit = StandardKit.objects.order_by("-pk").first()
        return Response(kits.kit_dict(kit), status=status.HTTP_201_CREATED)


class StandardKitDetailView(APIView):
    @extend_schema(request=dict, responses=dict)
    def patch(self, request, pk: int):
        _check(request.user)
        get_object_or_404(StandardKit, pk=pk)
        _run([{**request.data, "op": "update", "target": "kit", "id": pk}], request.user)
        return Response(kits.kit_dict(StandardKit.objects.get(pk=pk)))

    def delete(self, request, pk: int):
        _check(request.user)
        get_object_or_404(StandardKit, pk=pk)
        _run([{"op": "remove", "target": "kit", "id": pk}], request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuxInstrumentsView(APIView):
    @extend_schema(request=dict, responses={201: dict})
    def post(self, request):
        _check(request.user)
        _run([{**request.data, "op": "add", "target": "aux"}], request.user)
        return Response(kits.aux_dict(AuxInstrument.objects.order_by("-pk").first()), status=status.HTTP_201_CREATED)


class AuxInstrumentDetailView(APIView):
    @extend_schema(request=dict, responses=dict)
    def patch(self, request, pk: int):
        _check(request.user)
        get_object_or_404(AuxInstrument, pk=pk)
        _run([{**request.data, "op": "update", "target": "aux", "id": pk}], request.user)
        return Response(kits.aux_dict(AuxInstrument.objects.get(pk=pk)))

    def delete(self, request, pk: int):
        _check(request.user)
        get_object_or_404(AuxInstrument, pk=pk)
        _run([{"op": "remove", "target": "aux", "id": pk}], request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StandardKitsImportView(APIView):
    """POST multipart file=<docx> — обновить перечень по новому Word-файлу метролога."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(request=None, responses=dict)
    def post(self, request):
        _check(request.user)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"detail": "Приложите файл перечня (.docx)"})
        if upload.size > MAX_DOCX:
            raise ValidationError({"detail": "Файл больше 15 МБ"})
        try:
            report = kits.import_docx(upload.read(), request.user)
        except kits.KitError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(report)
