"""Веб-журнал и нормоконтроль.

Журнал — это выборка поверок с фильтрами, а не таблица. Экран нормоконтроля
показывает, что даст пересчёт номеров, прежде чем метролог его применит,
и даёт подписать пачку.
"""

from __future__ import annotations

import django_filters as filters
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.verification import journal, numbering
from apps.verification.models import (
    NumberingScope,
    Protocol,
    ProtocolStatus,
    Verification,
    VerificationStatus,
)


class JournalFilter(filters.FilterSet):
    """Фильтры журнала — то, чем метролог пользуется каждый день."""

    date_from = filters.DateFilter(field_name="verified_at", lookup_expr="date__gte")
    date_to = filters.DateFilter(field_name="verified_at", lookup_expr="date__lte")
    verifier = filters.NumberFilter(field_name="verifier_id")
    suitable = filters.BooleanFilter(field_name="suitable")
    status = filters.ChoiceFilter(choices=VerificationStatus.choices)
    protocol_status = filters.ChoiceFilter(
        field_name="protocol__status", choices=ProtocolStatus.choices
    )
    family = filters.NumberFilter(field_name="instrument__si_type__family_id")
    needs_review = filters.BooleanFilter(field_name="needs_review")
    q = filters.CharFilter(method="filter_search", label="Поиск")

    class Meta:
        model = Verification
        fields: list[str] = []

    def filter_search(self, queryset, name, value):
        """Заводской номер, наименование СИ, адрес или номер протокола."""
        value = (value or "").strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(instrument__serial_number__icontains=value)
            | Q(instrument__si_type__name__icontains=value)
            | Q(instrument__site__address__icontains=value)
            | Q(instrument__owner__name__icontains=value)
        )


class JournalRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    protocol_number = serializers.CharField()
    protocol_status = serializers.CharField()
    verified_at = serializers.DateTimeField()
    next_verification_date = serializers.DateField(allow_null=True)
    si_name = serializers.CharField()
    si_registry_number = serializers.CharField()
    serial_number = serializers.CharField()
    owner = serializers.CharField()
    address = serializers.CharField()
    verifier = serializers.CharField()
    suitable = serializers.BooleanField()
    status = serializers.CharField()
    needs_review = serializers.BooleanField()
    journal_note = serializers.CharField()

    @staticmethod
    def from_verification(verification: Verification) -> dict:
        protocol = getattr(verification, "protocol", None)
        return {
            "id": verification.pk,
            "protocol_number": protocol.full_number if protocol else "",
            "protocol_status": protocol.status if protocol else "",
            "verified_at": verification.verified_at,
            "next_verification_date": verification.next_verification_date,
            "si_name": verification.instrument.si_type.name,
            "si_registry_number": verification.instrument.si_type.registry_number,
            "serial_number": verification.instrument.serial_number,
            "owner": str(verification.instrument.owner) if verification.instrument.owner_id else "",
            "address": str(verification.instrument.site) if verification.instrument.site_id else "",
            "verifier": verification.verifier.full_name,
            "suitable": verification.suitable,
            "status": verification.status,
            "needs_review": verification.needs_review,
            "journal_note": (verification.results or {}).get("journal_note", ""),
        }


class JournalView(ListAPIView):
    """GET /api/journal/ — журнал с фильтрами и постраничной выдачей."""

    serializer_class = JournalRowSerializer
    filterset_class = JournalFilter

    def get_queryset(self):
        return journal.journal_queryset()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = [JournalRowSerializer.from_verification(v) for v in (page or queryset)]
        if page is not None:
            return self.get_paginated_response(rows)
        return Response(rows)


class JournalExportView(APIView):
    """GET /api/journal/export/ — выгрузка в `.xlsx` формата ФИФ ОЕИ."""

    @extend_schema(
        parameters=[
            OpenApiParameter("date_from", str), OpenApiParameter("date_to", str),
            OpenApiParameter("verifier", int), OpenApiParameter("q", str),
        ],
        responses={200: None},
    )
    def get(self, request):
        queryset = JournalFilter(request.query_params, queryset=journal.journal_queryset()).qs
        # Журнал читается как хронология, поэтому выгрузка идёт по возрастанию.
        content = journal.export_xlsx(queryset.order_by("verified_at", "id"))

        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        name = journal.filename()
        response["Content-Disposition"] = f'attachment; filename*=UTF-8\'\'{name}'
        return response


class NormocontrolScopesView(APIView):
    """GET /api/normocontrol/scopes/ — что ждёт нормоконтроля."""

    def get(self, request):
        scopes = NumberingScope.objects.select_related("series", "employee").order_by(
            "series__code", "employee__full_name", "-year"
        )
        return Response([journal.normocontrol_summary(scope) for scope in scopes])


class NumberingPreviewView(APIView):
    """POST /api/normocontrol/scopes/{id}/preview/

    Показывает, какие номера присвоятся и что сдвинется, ничего не меняя.
    """

    def post(self, request, pk: int):
        scope = get_object_or_404(NumberingScope, pk=pk)
        result = numbering.assign_numbers(scope, dry_run=True)
        return Response(
            {
                "changed": result.changed,
                "sealed_high_water": result.sealed_high_water,
                "assigned": [
                    {"verification": p.verification_id, "seq": p.seq,
                     "verified_at": p.verification.verified_at}
                    for p in result.assigned
                ],
                "renumbered": [
                    {"verification": p.verification_id, "from": old, "to": new,
                     "verified_at": p.verification.verified_at}
                    for p, old, new in result.renumbered
                ],
                "lines": result.describe(),
            }
        )


class NumberingApplyView(APIView):
    """POST /api/normocontrol/scopes/{id}/assign/ — применить пересчёт."""

    def post(self, request, pk: int):
        scope = get_object_or_404(NumberingScope, pk=pk)
        try:
            result = numbering.assign_numbers(scope)
        except numbering.NumberingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "assigned": len(result.assigned),
                "renumbered": len(result.renumbered),
                "lines": result.describe(),
            }
        )


class SignProtocolsSerializer(serializers.Serializer):
    protocols = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class SignProtocolsView(APIView):
    """POST /api/normocontrol/sign/ — подписать пачку протоколов.

    Право подписи проверяется доменным слоем: аттестация должна действовать
    на дату поверки. Что не подписалось — возвращается с причиной, пачка
    целиком из-за одного протокола не падает.
    """

    def post(self, request):
        payload = SignProtocolsSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        employee = getattr(request.user, "employee", None)
        if employee is None:
            return Response(
                {"detail": "У учётной записи нет карточки сотрудника — подписывать некому"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        signed, refused = [], []
        for protocol in Protocol.objects.filter(pk__in=payload.validated_data["protocols"]):
            try:
                numbering.seal(protocol, signed_by=employee)
            except numbering.NumberingError as exc:
                refused.append({"protocol": protocol.pk, "detail": str(exc)})
            else:
                signed.append({"protocol": protocol.pk, "number": protocol.full_number})

        return Response({"signed": signed, "refused": refused})
