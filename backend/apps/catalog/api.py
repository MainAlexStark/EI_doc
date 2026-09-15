"""Подсказка типов СИ при заведении прибора.

Поверитель вводит номер в Госреестре или часть наименования — получает список
вариантов, в каждом обязательно изготовитель. Сначала идут типы, которые уже
заведены в системе (по ним есть история поверок), затем — кандидаты из ФИФ.
"""

from __future__ import annotations

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.arshin.fif import FifClient, FifUnavailable, mark_ambiguous
from apps.catalog.models import MeasurementFamily, PricingSettings, SiType


class SiTypeSuggestionSerializer(serializers.Serializer):
    source = serializers.CharField(help_text="local — уже в системе, fif — из реестра")
    si_type_id = serializers.IntegerField(allow_null=True)
    registry_number = serializers.CharField()
    name = serializers.CharField()
    manufacturer = serializers.CharField()
    notation = serializers.CharField(allow_blank=True)
    interval_months = serializers.IntegerField(allow_null=True)
    label = serializers.CharField()
    ambiguous = serializers.BooleanField(
        help_text="Наименование совпадает с другим кандидатом — выбирать только по изготовителю"
    )


class SiTypeSuggestView(APIView):
    """GET /api/catalog/si-types/suggest?q=СВК-15"""

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Номер в Госреестре или часть наименования"),
            OpenApiParameter("limit", int, description="Сколько вариантов вернуть, по умолчанию 20"),
        ],
        responses=SiTypeSuggestionSerializer(many=True),
    )
    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        limit = min(int(request.query_params.get("limit") or 20), 50)
        if len(query) < 2:
            return Response([], status=status.HTTP_200_OK)

        suggestions = self._local(query, limit)
        seen = {(item["registry_number"], item["manufacturer"].lower()) for item in suggestions}

        warning = None
        if len(suggestions) < limit:
            try:
                candidates = FifClient().search_types(query, limit=limit - len(suggestions))
            except FifUnavailable as exc:
                warning = str(exc)
            else:
                for item in mark_ambiguous(candidates):
                    key = (item["registry_number"], item["manufacturer"].lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    suggestions.append({"source": "fif", "si_type_id": None, **item})

        suggestions = self._flag_duplicates(suggestions)
        payload = {"results": suggestions}
        if warning:
            # Реестр недоступен — это не ошибка операции: тип заводится вручную.
            payload["warning"] = warning
        return Response(payload)

    @staticmethod
    def _local(query: str, limit: int) -> list[dict]:
        found = SiType.objects.filter(
            Q(registry_number__icontains=query)
            | Q(name__icontains=query)
            | Q(manufacturer__icontains=query),
            is_active=True,
        ).select_related("family")[:limit]

        return [
            {
                "source": "local",
                "si_type_id": si_type.pk,
                "registry_number": si_type.registry_number,
                "name": si_type.name,
                "manufacturer": si_type.manufacturer,
                "notation": "",
                "interval_months": si_type.verification_interval_months,
                "label": " · ".join(
                    part for part in (
                        si_type.name,
                        f"№ {si_type.registry_number}" if si_type.registry_number else "",
                        si_type.manufacturer or "изготовитель не указан",
                    ) if part
                ),
                "ambiguous": False,
            }
            for si_type in found
        ]

    @staticmethod
    def _flag_duplicates(suggestions: list[dict]) -> list[dict]:
        """Пометить совпадающие наименования по всему объединённому списку."""
        counts: dict[str, int] = {}
        for item in suggestions:
            key = item["name"].strip().lower()
            counts[key] = counts.get(key, 0) + 1
        for item in suggestions:
            if counts[item["name"].strip().lower()] > 1:
                item["ambiguous"] = True
        return suggestions


# ---------------------------------------------------------------------------
# Семейства СИ для формы заявки на сайте
# ---------------------------------------------------------------------------
class FamilyOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    price = serializers.DecimalField(max_digits=9, decimal_places=2)
    requires_time_slot = serializers.BooleanField()


class FamilyOptionsView(APIView):
    """GET /api/catalog/families/ — публичный список семейств СИ для формы заявки.

    Заявитель выбирает из него, что нужно поверить (мультивыбор + количество),
    цена — ориентир, не привязана к конкретному прибору из Госреестра: столько
    вариантов на форме не нужно, это делает уже поверитель при заведении наряда.
    """

    permission_classes = [AllowAny]
    throttle_classes = []  # справочник, не заявка — не делит бюджет с анти-спамом формы

    @extend_schema(responses={200: FamilyOptionSerializer(many=True)})
    def get(self, request):
        families = MeasurementFamily.objects.order_by("name")
        settings_obj = PricingSettings.current()
        return Response({
            "families": FamilyOptionSerializer(families, many=True).data,
            "priority_discount_percent": settings_obj.priority_discount_percent,
        })
