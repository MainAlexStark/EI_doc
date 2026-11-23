"""REST для экрана «Аттестация ИО»."""

from __future__ import annotations

import datetime as dt

from django.db.models import Count, Q
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


def program_data(program: AttestationProgram, *, with_plan: bool = True) -> dict:
    data = {
        "id": program.id, "title": program.title, "equipment_kind": program.equipment_kind, "note": program.note,
        "needs_review": program.needs_review, "status": "draft" if program.needs_review else "reviewed",
        "reviewed_by": _user_name(program.reviewed_by), "reviewed_at": program.reviewed_at,
        "created_by": _user_name(program.created_by), "created_via_assistant": program.created_via_assistant,
        "source_attestation_id": program.source_attestation_id, "is_active": program.is_active,
        "used": getattr(program, "used", None), "sources": program.sources,
        "plan_check": program.plan_check or None,
    }
    if with_plan:
        data["plan"] = program.plan
    return data


class ProgramListView(APIView):
    """GET/POST /api/attestations/programs/ — шаблоны планов аттестации по виду ИО.

    GET ?equipment_kind=термостат&status=draft|reviewed&all=1 (с выключенными)&plan=0 (без плана).
    POST {"title", "equipment_kind", "plan", "note"?} — завести шаблон руками.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = AttestationProgram.objects.select_related("reviewed_by", "created_by").annotate(used=Count("attestations"))
        if request.query_params.get("all") != "1":
            qs = qs.filter(is_active=True)
        kind = (request.query_params.get("equipment_kind") or "").strip()
        if kind:
            qs = qs.filter(Q(equipment_kind__icontains=kind) | Q(title__icontains=kind))
        state = request.query_params.get("status")
        if state in ("draft", "reviewed"):
            qs = qs.filter(needs_review=state == "draft")
        with_plan = request.query_params.get("plan") != "0"
        return Response([program_data(p, with_plan=with_plan) for p in qs])

    def post(self, request):
        try:
            program = service.save_program(request.user, request.data, via_assistant=False)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(program_data(program), status=status.HTTP_201_CREATED)


class ProgramDetailView(APIView):
    """GET/PATCH /api/attestations/programs/{id}/ — шаблон; PATCH — title, equipment_kind, plan, note, is_active."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(program_data(get_object_or_404(AttestationProgram, pk=pk)))

    def patch(self, request, pk):
        program = get_object_or_404(AttestationProgram, pk=pk)
        if set(request.data) == {"is_active"}:
            if not service.can_edit(request.user):
                raise PermissionDenied("Нет права менять шаблоны аттестации")
            program.is_active = bool(request.data["is_active"])
            program.save(update_fields=["is_active"])
            return Response(program_data(program))
        merged = {"title": program.title, "equipment_kind": program.equipment_kind, "plan": program.plan,
                  "note": program.note, **{k: v for k, v in request.data.items() if k in ("title", "equipment_kind", "plan", "note")}}
        check = program.plan_check or None
        if check:
            try:
                same_plan = calc.clean_plan(merged["plan"]) == program.plan
            except calc.PlanError:
                same_plan = False
            if not same_plan:
                check = {**check, "stale": True}  # сверка была для прежнего плана
        try:
            program = service.save_program(request.user, merged, sources=program.sources, program_id=program.pk,
                                           via_assistant=program.created_via_assistant, plan_check=check)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(program_data(program))


class ProgramApproveView(APIView):
    """POST /api/attestations/programs/{id}/approve/ — метролог проверил шаблон."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        program = service.approve_program(get_object_or_404(AttestationProgram, pk=pk), request.user)
        return Response(program_data(program))


class ProgramCheckView(APIView):
    """POST /api/attestations/programs/{id}/check/ — пересверить формулы плана на числах примера.

    {"example": {source, modes: {режим: {display?, readings: {точка: [..]}, expected: {характеристика: "5,69"
    | {точка: "…"}}}}}} → шаблон с новым plan_check. Числа примера не сохраняются — только отчёт.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        program = get_object_or_404(AttestationProgram, pk=pk)
        example = request.data.get("example")
        if not example:
            return _fail(ValueError("нужны числа примера: example{source, modes{режим: {readings, expected}}}"))
        try:
            program = service.check_program(program, request.user, example)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(program_data(program))


class ProgramApplyView(APIView):
    """POST /api/attestations/programs/{id}/apply/ — новая аттестация ИО по шаблону.

    {"equipment": {name, serial_number, manufacturer, year, owner, location, inventory_number},
     "kind"?: primary|periodic|repeat, "commission"?, "instruments"?, "texts"?} → карточка аттестации (201).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        program = get_object_or_404(AttestationProgram, pk=pk)
        data = {k: v for k, v in request.data.items() if k in ("equipment", "kind", "commission", "instruments", "texts")}
        try:
            att = service.apply_program(program, request.user, data)
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(detail(att, request.user), status=status.HTTP_201_CREATED)


class SaveAsProgramView(APIView):
    """POST /api/attestations/{id}/save-as-program/ {"title"?, "equipment_kind"?, "note"?} — шаблон из плана аттестации."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        att = get_object_or_404(EquipmentAttestation, pk=pk)
        try:
            program = service.program_from_attestation(
                att, request.user, title=str(request.data.get("title") or ""),
                equipment_kind=str(request.data.get("equipment_kind") or ""), note=str(request.data.get("note") or ""),
            )
        except service.AttestationError as exc:
            return _fail(exc)
        return Response(program_data(program), status=status.HTTP_201_CREATED)


class AttestationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = EquipmentAttestation.objects.only(
            "id", "number", "year", "status", "kind", "equipment", "performed_on", "protocol_date", "suitable",
            "plan_approved_at", "created_via_assistant", "created_at",
        )
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
