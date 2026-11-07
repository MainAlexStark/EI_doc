"""API отправки в Аршин: очередь готовых протоколов, пакеты, отметки и сверка."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django_sendfile import sendfile
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.arshin import submission as service
from apps.arshin.fif import FifUnavailable
from apps.arshin.models import ArshinSubmission, ArshinSubmissionItem
from apps.core.permissions import HasPerm

ARSHIN = [HasPerm.of("arshin.submit", safe_perm="verification.view_journal")]


def _item(item: ArshinSubmissionItem) -> dict:
    return {
        "id": item.pk,
        "protocol": item.protocol_id,
        "number": item.protocol.full_number,
        "serial_number": item.protocol.verification.instrument.serial_number,
        "si_name": item.protocol.verification.instrument.si_type.name,
        "status": item.status,
        "status_display": item.get_status_display(),
        "fif_record_number": item.fif_record_number,
        "error": item.error,
    }


def _submission(sub: ArshinSubmission, *, items: bool = False) -> dict:
    counts: dict[str, int] = {}
    for value in sub.items.values_list("status", flat=True):
        counts[value] = counts.get(value, 0) + 1
    data = {
        "id": sub.pk,
        "status": sub.status,
        "status_display": sub.get_status_display(),
        "created_at": sub.created_at,
        "created_by": getattr(sub.created_by, "email", ""),
        "uploaded_at": sub.uploaded_at,
        "reconciled_at": sub.reconciled_at,
        "sha256": sub.sha256,
        "counts": counts,
        "total": sum(counts.values()),
    }
    if items:
        data["items"] = [
            _item(i) for i in sub.items.select_related("protocol__verification__instrument__si_type",
                                                        "protocol__scope__series", "protocol__scope__employee")
        ]
    return data


class QueueView(APIView):
    """GET /api/arshin/queue/ — подписанные протоколы, ещё не отправленные, с готовностью."""

    permission_classes = ARSHIN

    def get(self, request):
        rows = []
        for protocol in service.eligible_protocols()[:500]:
            verification = protocol.verification
            rows.append({
                "protocol": protocol.pk,
                "number": protocol.full_number,
                "verified_at": verification.verified_at,
                "si_name": verification.instrument.si_type.name,
                "serial_number": verification.instrument.serial_number,
                "suitable": verification.suitable,
                "problems": service.problems(protocol),
            })
        return Response({"sign_cipher": service.sign_cipher(), "protocols": rows})


class CreateSerializer(serializers.Serializer):
    protocols = serializers.ListField(child=serializers.IntegerField(), required=False, allow_empty=False)


class SubmissionListView(APIView):
    """GET /api/arshin/submissions/ — пакеты; POST — собрать новый."""

    permission_classes = ARSHIN

    def get(self, request):
        return Response([_submission(s) for s in ArshinSubmission.objects.select_related("created_by")[:100]])

    def post(self, request):
        payload = CreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = service.create_submission(payload.validated_data.get("protocols"), user=request.user)
        if result.submission is None:
            return Response({"detail": "Ни один протокол не готов к отправке", "refused": result.refused},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"submission": _submission(result.submission, items=True), "refused": result.refused},
                        status=status.HTTP_201_CREATED)


class SubmissionDetailView(APIView):
    permission_classes = ARSHIN

    def get(self, request, pk: int):
        return Response(_submission(get_object_or_404(ArshinSubmission, pk=pk), items=True))


class SubmissionXmlView(APIView):
    permission_classes = ARSHIN

    def get(self, request, pk: int):
        sub = get_object_or_404(ArshinSubmission, pk=pk)
        if not sub.xml:
            return Response({"detail": "XML не сформирован"}, status=status.HTTP_404_NOT_FOUND)
        return sendfile(request, sub.xml.path, attachment=True,
                        attachment_filename=f"arshin-{sub.pk:05d}.xml", mimetype="application/xml")


class SubmissionActionView(APIView):
    """POST /api/arshin/submissions/{id}/{uploaded|cancel|reconcile}/"""

    permission_classes = [HasPerm.of("arshin.submit")]

    def post(self, request, pk: int, action: str):
        sub = get_object_or_404(ArshinSubmission, pk=pk)
        try:
            if action == "uploaded":
                service.mark_uploaded(sub)
            elif action == "cancel":
                service.cancel(sub)
            elif action == "reconcile":
                outcome = service.reconcile(sub)
                return Response({**outcome, "submission": _submission(sub, items=True)})
            else:
                return Response({"detail": "Неизвестное действие"}, status=status.HTTP_404_NOT_FOUND)
        except service.SubmissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except FifUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        sub.refresh_from_db()
        return Response(_submission(sub, items=True))


class ItemResultSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    fif_record_number = serializers.CharField(required=False, allow_blank=True)
    reason = serializers.CharField(required=False, allow_blank=True)


class ItemResultView(APIView):
    """POST /api/arshin/items/{id}/result/ — итог из ЛК вручную: номер записи или отказ."""

    permission_classes = [HasPerm.of("arshin.submit")]

    def post(self, request, pk: int):
        item = get_object_or_404(ArshinSubmissionItem, pk=pk)
        payload = ItemResultSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            if data["accepted"]:
                service.accept(item, data.get("fif_record_number", ""))
            else:
                service.reject(item, data.get("reason", ""))
        except service.SubmissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        item.refresh_from_db()
        return Response(_item(item))
