"""REST шаблонов поверки и скачивание протокола поверки (claude/templates.md)."""

from __future__ import annotations

from urllib.parse import quote

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.equipment import calc
from apps.verification import templates
from apps.verification.models import Verification, VerificationTemplate

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
DEFAULT_CONDITION_KEYS = {"temperature", "humidity", "pressure"}


def _name(user) -> str:
    if not user:
        return ""
    employee = getattr(user, "employee", None)
    return employee.full_name if employee else user.email


def template_json(t: VerificationTemplate, *, full: bool = False) -> dict:
    plan = t.plan
    data = {
        "id": t.pk, "title": t.title, "family": t.family_id, "family_name": t.family.name,
        "si_type": t.si_type_id, "si_type_name": str(t.si_type) if t.si_type_id else "",
        "needs_review": t.needs_review, "is_active": t.is_active, "updated_at": t.updated_at,
        "created_via_assistant": t.created_via_assistant, "note": t.note,
        "plan": {
            "si_kind": plan["si_kind"], "method": plan["method"], "quantity": plan.get("quantity", ""),
            "unit": plan.get("unit", ""), "repeats": plan["repeats"], "params": plan["params"],
            "operations": plan["operations"],
            "modes": [{"key": m["key"], "label": m["label"], "setpoint": m["setpoint"]} for m in plan["modes"]],
            "points": [{"key": p["key"], "label": p.get("label") or p["key"]} for p in plan["points"]],
            "characteristics": [
                {"key": c.get("key"), "label": c.get("label"), "text": c.get("text", ""), "per": c.get("per", "mode")}
                for c in plan["characteristics"]
            ],
            # Условия сверх температуры/влажности/давления (они уже есть у поверки) — их вносит поверитель.
            "extra_conditions": [c for c in plan.get("conditions") or [] if c.get("key") not in DEFAULT_CONDITION_KEYS],
            "uses_display": any("display" in str(c.get("expr", "")) for c in plan["characteristics"]),
        },
    }
    if full:
        data.update(
            full_plan=plan, sources=t.sources, plan_check=t.plan_check, created_by=_name(t.created_by),
            reviewed_by=_name(t.reviewed_by), reviewed_at=t.reviewed_at,
        )
    return data


def _file(data: bytes, fmt: str, name: str) -> HttpResponse:
    response = HttpResponse(data, content_type=MIME[fmt])
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return response


class TemplateListView(APIView):
    """GET /api/verification-templates/ — активные шаблоны (экран поверителя кэширует их для офлайна)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = VerificationTemplate.objects.select_related("family", "si_type")
        if request.query_params.get("all") != "1":
            qs = qs.filter(is_active=True)
        if request.query_params.get("family"):
            qs = qs.filter(family_id=request.query_params["family"])
        return Response([template_json(t) for t in qs.order_by("family__name", "title")])


class TemplateDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk: int):
        template = get_object_or_404(VerificationTemplate.objects.select_related("family", "si_type"), pk=pk)
        return Response(template_json(template, full=True))


class TemplateApproveView(APIView):
    """POST /api/verification-templates/{id}/approve/ — метролог проверил шаблон."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk: int):
        template = get_object_or_404(VerificationTemplate, pk=pk)
        templates.approve(template, request.user)
        return Response(template_json(template, full=True))


class TemplatePreviewView(APIView):
    """GET /api/verification-templates/{id}/preview.pdf|docx — пустой протокол по шаблону."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk: int, fmt: str):
        if fmt not in MIME:
            return Response({"detail": "Формат — pdf или docx"}, status=status.HTTP_400_BAD_REQUEST)
        template = get_object_or_404(VerificationTemplate, pk=pk)
        try:
            data = templates.render(templates.preview_spec(template.plan, title=template.title), fmt)
        except Exception as exc:  # noqa: BLE001 — нет typst и т. п.: показать человеку причину
            return Response({"detail": f"Не удалось собрать {fmt}: {exc}"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return _file(data, fmt, f"Шаблон — {template.title}.{fmt}")


class VerificationTemplateView(APIView):
    """GET/POST /api/verifications/{id}/template/

    Какой шаблон у этой поверки, что уже известно о параметрах прибора (из типа и прошлых поверок)
    и поверяемые точки числами. POST {params} — пересчитать точки по внесённым параметрам.
    """

    permission_classes = [IsAuthenticated]

    def _answer(self, verification: Verification, given: dict):
        if not templates.uses_template(verification):
            return Response({"engine": "water_meter", "template": None})
        template = templates.template_for(verification.instrument.si_type)
        try:
            values, missing = templates.known_params(template.plan, verification.instrument, given)
        except templates.MeasurementInputError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        from_type = {
            p["key"] for p in template.plan["params"]
            if p["from_type"] and (verification.instrument.si_type.limits or {}).get(p["from_type"]) not in (None, "")
        }
        modes = []
        if not missing:
            try:
                plan = templates.resolve(template.plan, values)
            except templates.TemplateError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            modes = [
                {"key": m["key"], "label": m["label"],
                 "setpoint": calc.fmt(calc.number(m["setpoint"]), max(calc.places(calc.number(m["setpoint"])), 0))}
                for m in plan["modes"]
            ]
        saved = verification.measurements if (verification.measurements or {}).get("engine") == templates.ENGINE else {}
        return Response({
            "engine": templates.ENGINE, "template": template_json(template),
            "params": {k: str(v) for k, v in values.items()}, "params_from_type": sorted(from_type),
            "missing": [p["key"] for p in missing], "modes": modes,
            "saved": {k: saved.get(k) for k in ("kind", "operations", "readings", "conditions", "manual_unsuitable",
                                                 "manual_unsuitability_reason")} if saved else None,
        })

    def get(self, request, pk: int):
        verification = get_object_or_404(Verification.objects.select_related("instrument__si_type__family"), pk=pk)
        return self._answer(verification, {})

    def post(self, request, pk: int):
        verification = get_object_or_404(Verification.objects.select_related("instrument__si_type__family"), pk=pk)
        given = request.data.get("params") if isinstance(request.data.get("params"), dict) else {}
        return self._answer(verification, given)


class VerificationProtocolView(APIView):
    """GET /api/verifications/{id}/protocol.pdf|docx — протокол поверки (черновик, пока не подписан).

    По шаблону — Word и PDF из одного каркаса; счётчики воды — прежний Typst-шаблон (только PDF).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk: int, fmt: str):
        if fmt not in MIME:
            return Response({"detail": "Формат — pdf или docx"}, status=status.HTTP_400_BAD_REQUEST)
        verification = get_object_or_404(
            Verification.objects.select_related("instrument__si_type__family", "verifier"), pk=pk
        )
        protocol = getattr(verification, "protocol", None)
        number = protocol.full_number if protocol and protocol.seq is not None else ""
        name = f"Протокол {number or 'черновик'} — {verification.instrument.serial_number}.{fmt}"
        try:
            if (verification.measurements or {}).get("engine") == templates.ENGINE:
                data = templates.render(templates.protocol_spec(verification), fmt)
            else:
                if fmt != "pdf":
                    return Response({"detail": "Протокол счётчика воды выпускается только в PDF"},
                                    status=status.HTTP_400_BAD_REQUEST)
                from apps.verification import render

                data = render.render_for(verification, number=number or "б/н")
        except templates.MeasurementInputError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001 — нет typst, нет измерений и т. п.
            return Response({"detail": f"Не удалось собрать {fmt}: {exc}"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return _file(data, fmt, name)
