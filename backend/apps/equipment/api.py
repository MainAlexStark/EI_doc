"""REST для экрана «Аттестация ИО»."""

from __future__ import annotations

import datetime as dt

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.equipment import calc, service
from apps.equipment.models import AttestationProgram, EquipmentAttestation


def _user_name(user) -> str:
    if not user:
        return ""
    employee = getattr(user, "employee", None)
    return employee.full_name if employee else user.email


def summary(att: EquipmentAttestation) -> dict:
    return {
        "id": att.pk, "number": att.full_number, "status": att.status, "kind": att.kind,
        "name": att.equipment.get("name", ""), "serial_number": att.equipment.get("serial_number", ""),
        "owner": att.equipment.get("owner", ""), "performed_on": att.performed_on,
        "protocol_date": att.protocol_date, "suitable": att.suitable, "plan_approved": att.plan_approved,
        "created_via_assistant": att.created_via_assistant, "created_at": att.created_at,
    }


def detail(att: EquipmentAttestation, user) -> dict:
    state = service.preview(att)
    on = att.protocol_date or timezone.localdate()
    return {
        **summary(att),
        "equipment": att.equipment, "commission": att.commission, "instruments": att.instruments,
        "plan": att.plan, "texts": att.texts, "sources": att.sources,
        "conditions": att.conditions, "measurements": att.measurements,
        "plan_approved_by": _user_name(att.plan_approved_by), "plan_approved_at": att.plan_approved_at,
        "measurements_by": _user_name(att.measurements_by), "measurements_at": att.measurements_at,
        "signed_by": _user_name(att.signed_by), "signed_at": att.signed_at, "sha256": att.sha256,
        "program": att.program.title if att.program else "",
        "calc": state,
        "sign_problems": [] if att.is_signed else service.sign_problems(att, on),
        "can_edit": service.can_edit(user) and not att.is_signed,
        "can_review": service.is_reviewer(user) and not att.is_signed,
        "plan_check": att.plan_check,
        "plan_tolerances": {c["key"]: calc.plan_tolerance_text(c["tolerance"]) for c in att.plan.get("characteristics", [])},
    }


def _fail(exc: Exception) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProgramListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([
            {"id": p.id, "title": p.title, "equipment_kind": p.equipment_kind, "note": p.note,
             "needs_review": p.needs_review, "plan": p.plan}
            for p in AttestationProgram.objects.filter(is_active=True)
        ])


class AttestationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = EquipmentAttestation.objects.all()
        q = (request.query_params.get("q") or "").strip()
        for word in q.split():
            qs = qs.filter(
                Q(equipment__name__icontains=word) | Q(equipment__serial_number__icontains=word)
                | Q(equipment__owner__icontains=word)
            )
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response([summary(a) for a in qs[:300]])

    def post(self, request):
        data = dict(request.data)
        program = None
        if data.get("program") and not data.get("plan"):
            program = get_object_or_404(AttestationProgram, pk=data["program"])
            data["plan"] = program.plan
        try:
            att = service.create(request.user, data, program=program)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(detail(att, request.user), status=status.HTTP_201_CREATED)


class AttestationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(detail(get_object_or_404(EquipmentAttestation, pk=pk), request.user))

    def patch(self, request, pk):
        att = get_object_or_404(EquipmentAttestation, pk=pk)
        try:
            att = service.update(att, request.user, request.data)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(detail(att, request.user))


class MeasurementsView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        att = get_object_or_404(EquipmentAttestation, pk=pk)
        try:
            att, _ = service.save_measurements(att, request.user, request.data)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(detail(att, request.user))


class ApprovePlanView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        att = get_object_or_404(EquipmentAttestation, pk=pk)
        try:
            att = service.approve_plan(att, request.user)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(detail(att, request.user))


class SignView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        att = get_object_or_404(EquipmentAttestation, pk=pk)
        raw = request.data.get("protocol_date")
        try:
            on = dt.date.fromisoformat(raw) if raw else None
            att = service.sign(att, request.user, on)
        except ValueError as exc:  # AttestationError — тоже ValueError
            return _fail(exc)
        return Response(detail(att, request.user))


MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


class ProtocolFileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, fmt):
        if fmt not in MIME:
            raise PermissionDenied("Формат — docx или pdf")
        att = get_object_or_404(EquipmentAttestation, pk=pk)
        try:
            data = service.render(att, fmt)
        except Exception as exc:  # noqa: BLE001 — нет typst и т. п.: показать человеку причину
            return Response({"detail": f"Не удалось собрать {fmt}: {exc}"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = HttpResponse(data, content_type=MIME[fmt])
        from urllib.parse import quote

        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(service.filename(att, fmt))}"
        return response
