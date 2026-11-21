"""GET /api/core/today/ — сводка главного экрана «Сегодня» для того, кто вошёл.

Одним запросом к API и ~10 запросами к базе — экран открывают каждое утро все сразу.
Руководство (владелец, руководитель, метролог, менеджер) видит фирму целиком,
поверитель — своё: свои адреса, свои приёмки, свои командировки.

Ответ::

    {"date": "2026-11-21", "scope": "company" | "mine",
     "requests": {"new": 3, "mine": 1},                   # новые заявки / подобранные на меня
     "stops": {"total": 6, "done": 2, "waiting": 4},      # мои адреса сегодня (выезды в городе и командировки)
     "intake": {"in_work": 4, "ready_to_issue": 2},       # приёмки: в работе / готовы к выдаче
     "trips": {"count": 1, "items": [{"id", "title", "date_start", "date_end", "kind"}]},
     "procurements": {"active": 5},
     "review": {"si_types": 2, "verification_templates": 1, "attestation_templates": 0},
     "protocols": {"drafts": 7, "rejected": 1, "on_review": 3}}   # мои поверки по статусам
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Role

COMPANY_ROLES = {Role.OWNER, Role.HEAD, Role.METROLOGIST, Role.MANAGER}


def summary(user) -> dict:
    from apps.catalog.models import SiType
    from apps.equipment.models import AttestationProgram
    from apps.hub.models import Request, RequestStatus
    from apps.logistics.models import ProcurementStatus, StopStatus, Trip, TripKind, TripStatus, TripStop
    from apps.logistics.api import visible_procurements
    from apps.verification.models import (
        Verification,
        VerificationStatus,
        VerificationTemplate,
        WorkOrder,
        WorkOrderKind,
        WorkOrderStatus,
    )

    today = timezone.localdate()
    company = bool(user.is_superuser or user.role in COMPANY_ROLES)
    me = getattr(user, "employee", None)
    me_id = me.pk if me else None

    requests = Request.objects.filter(status__in=[RequestStatus.NEW, RequestStatus.ROUTED]).aggregate(
        new=Count("id"), mine=Count("id", filter=Q(suggested_employee_id=me_id)),
    )

    stops = {"total": 0, "done": 0, "waiting": 0}
    if me_id:
        stops = TripStop.objects.filter(
            Q(planned_date=today) | Q(planned_date__isnull=True, trip__date_start=today),
            Q(work_order__assigned_employee_id=me_id) | Q(work_order__isnull=True, trip__lead_id=me_id),
            trip__status__in=[TripStatus.PLANNED, TripStatus.ACTIVE],
        ).aggregate(
            total=Count("id"), done=Count("id", filter=Q(status=StopStatus.DONE)),
            waiting=Count("id", filter=Q(status=StopStatus.PLANNED)),
        )

    lab = WorkOrder.objects.filter(kind=WorkOrderKind.LAB, issued_at__isnull=True).exclude(
        status=WorkOrderStatus.CANCELLED
    )
    if not company:
        lab = lab.filter(assigned_employee_id=me_id)
    in_work = ready_to_issue = 0
    for total, ready in lab.annotate(
        n_total=Count("verifications"),
        n_ready=Count("verifications", filter=Q(verifications__status__in=[
            VerificationStatus.READY, VerificationStatus.ACCEPTED])),
    ).values_list("n_total", "n_ready"):
        if total and ready == total:
            ready_to_issue += 1
        else:
            in_work += 1

    trips_qs = Trip.objects.filter(
        status__in=[TripStatus.PLANNED, TripStatus.ACTIVE], date_start__lte=today,
    ).filter(Q(date_end__gte=today) | Q(date_end__isnull=True)).exclude(kind=TripKind.CITY)
    if not company:
        trips_qs = trips_qs.filter(Q(lead_id=me_id) | Q(members=me_id)).distinct()
    trips = [
        {"id": t.pk, "title": t.title, "date_start": t.date_start, "date_end": t.date_end, "kind": t.kind}
        for t in trips_qs.order_by("date_start", "id")[:20]
    ]

    procurements = visible_procurements(user).filter(
        status__in=[ProcurementStatus.PREPARING, ProcurementStatus.ACTIVE]
    ).count()

    by_status = {}
    if me_id:
        by_status = dict(
            Verification.objects.filter(verifier_id=me_id, status__in=[
                VerificationStatus.DRAFT, VerificationStatus.REJECTED, VerificationStatus.READY,
            ]).values_list("status").annotate(n=Count("id"))
        )

    return {
        "date": today,
        "scope": "company" if company else "mine",
        "requests": {"new": requests["new"], "mine": requests["mine"] if me_id else 0},
        "stops": stops,
        "intake": {"in_work": in_work, "ready_to_issue": ready_to_issue},
        "trips": {"count": len(trips), "items": trips},
        "procurements": {"active": procurements},
        "review": {
            "si_types": SiType.objects.filter(is_active=True, needs_review=True).count(),
            "verification_templates": VerificationTemplate.objects.filter(is_active=True, needs_review=True).count(),
            "attestation_templates": AttestationProgram.objects.filter(is_active=True, needs_review=True).count(),
        },
        "protocols": {
            "drafts": by_status.get(VerificationStatus.DRAFT, 0),
            "rejected": by_status.get(VerificationStatus.REJECTED, 0),
            "on_review": by_status.get(VerificationStatus.READY, 0),
        },
    }


class TodayView(APIView):
    def get(self, request):
        return Response(summary(request.user))
