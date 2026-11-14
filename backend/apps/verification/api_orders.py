"""Заказы — вкладка «Поверки»: заказчик привёз приборы, мы поверили и отдали.

Заказ — это наряд вида ``lab``: у него заказчик (юрлицо с ИНН), приборы
(поверки внутри наряда — тот же экран ввода, что и на выезде), акт приёма и акт
выдачи. Адрес объекта у заказа — юридический адрес заказчика: в журнал он
попадает как адрес владельца.

    GET   /api/clients/suggest/?q=            организации по примерному названию / началу ИНН
    GET   /api/clients/lookup/?inn=           организация по ИНН (своя база → DaData)
    POST  /api/orders/                        принять приборы: новый заказ
    PATCH /api/orders/{id}/                   контакт, примечание, закупка, заказчик
    POST  /api/orders/{id}/issue/             отдали заказчику (или отменить выдачу)
    GET   /api/orders/{id}/act/<kind>.pdf     акт приёма (receive) или выдачи (issue)
"""

from __future__ import annotations

from urllib.parse import quote

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Employee, Role
from apps.verification import party
from apps.verification.api_workorders import WorkOrderSerializer
from apps.verification.models import (
    Client,
    ClientKind,
    Site,
    VerificationStatus,
    WorkOrder,
    WorkOrderKind,
    WorkOrderStatus,
)
from apps.verification.render import ORG, RenderError, render_pdf

MANAGERS = {Role.OWNER, Role.HEAD, Role.METROLOGIST, Role.MANAGER}


def _employee(request) -> Employee:
    employee = getattr(request.user, "employee", None)
    if employee is None:
        raise PermissionDenied("У учётной записи нет привязанного сотрудника")
    return employee


def _order(request, pk: int) -> WorkOrder:
    order = get_object_or_404(WorkOrder.objects.select_related("client", "site", "procurement"), pk=pk)
    if order.kind != WorkOrderKind.LAB:
        raise NotFound("Это не заказ")
    if not (request.user.is_superuser or request.user.role in MANAGERS):
        employee = getattr(request.user, "employee", None)
        if employee is None or order.assigned_employee_id != employee.pk:
            raise NotFound("Заказ не найден")
    return order


class ClientSuggestView(APIView):
    def get(self, request):
        query = request.query_params.get("q", "")
        return Response({"results": [item.as_dict() for item in party.suggest(query)]})


class ClientLookupView(APIView):
    def get(self, request):
        inn = request.query_params.get("inn", "")
        found = party.lookup(inn)
        if found is None:
            return Response({"found": False, "valid": party.valid_inn("".join(c for c in inn if c.isdigit()))})
        return Response({"found": True, **found.as_dict()})


class OrderWriteSerializer(serializers.Serializer):
    inn = serializers.CharField(max_length=12, required=False, allow_blank=True)
    name = serializers.CharField(max_length=250, required=False, allow_blank=True)
    kpp = serializers.CharField(max_length=9, required=False, allow_blank=True)
    ogrn = serializers.CharField(max_length=15, required=False, allow_blank=True)
    address = serializers.CharField(max_length=350, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    contact = serializers.CharField(max_length=200, required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    procurement_id = serializers.IntegerField(required=False, allow_null=True)
    assigned_employee_id = serializers.IntegerField(required=False)

    def validate_inn(self, value: str) -> str:
        value = "".join(ch for ch in value if ch.isdigit())
        if value and not party.valid_inn(value):
            raise serializers.ValidationError("ИНН — 10 цифр у организации, 12 у ИП и физлица")
        return value


def _client(data: dict, current: Client | None = None) -> Client:
    inn, name = data.get("inn", ""), (data.get("name") or "").strip()
    client = current
    if inn and (client is None or client.inn != inn):
        client = Client.objects.filter(inn=inn).order_by("-created_at").first()
    if client is None and name and not inn:
        client = Client.objects.filter(name__iexact=name, inn="").exclude(kind=ClientKind.PERSON).first()
    if client is None:
        if not name:
            raise ValidationError({"detail": "Укажите ИНН или название заказчика"})
        client = Client(kind=ClientKind.ORG if len(inn) != 12 else ClientKind.PERSON, name=name, inn=inn)
    for field, key in (("name", "name"), ("kpp", "kpp"), ("ogrn", "ogrn"), ("legal_address", "address"), ("phone", "phone")):
        value = (data.get(key) or "").strip()
        if value:
            setattr(client, field, value)
    client.save()
    return client


def _site(client: Client) -> Site:
    address = client.legal_address or client.name
    site = Site.objects.filter(client=client, address=address).first()
    return site or Site.objects.create(client=client, address=address)


def _procurement(data: dict):
    if not data.get("procurement_id"):
        return None
    from apps.logistics.models import Procurement

    return get_object_or_404(Procurement, pk=data["procurement_id"])


class OrderCreateView(APIView):
    def post(self, request):
        me = _employee(request)
        payload = OrderWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        assignee = me
        if data.get("assigned_employee_id") and request.user.role in MANAGERS:
            assignee = get_object_or_404(Employee, pk=data["assigned_employee_id"], is_active=True)
        with transaction.atomic():
            client = _client(data)
            order = WorkOrder.objects.create(
                kind=WorkOrderKind.LAB, client=client, site=_site(client), assigned_employee=assignee,
                status=WorkOrderStatus.IN_PROGRESS, scheduled_date=timezone.localdate(),
                contact=(data.get("contact") or "").strip(), note=data.get("note", ""),
                procurement=_procurement(data), received_at=timezone.now(),
            )
        return Response(WorkOrderSerializer(order).data, status=status.HTTP_201_CREATED)


class OrderDetailView(APIView):
    def patch(self, request, pk: int):
        order = _order(request, pk)
        payload = OrderWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        with transaction.atomic():
            if any(key in data for key in ("inn", "name", "kpp", "ogrn", "address", "phone")):
                client = _client(data, order.client)
                if client.pk != order.client_id or order.site.address != (client.legal_address or client.name):
                    order.client, order.site = client, _site(client)
            for field in ("contact", "note"):
                if field in data:
                    setattr(order, field, data[field].strip() if field == "contact" else data[field])
            if "procurement_id" in data:
                order.procurement = _procurement(data)
            order.save()
        return Response(WorkOrderSerializer(order).data)


class OrderIssueView(APIView):
    """POST /api/orders/{id}/issue/ {"issued": true|false}"""

    def post(self, request, pk: int):
        order = _order(request, pk)
        issued = bool(request.data.get("issued", True))
        if issued and not order.verifications.exists():
            raise ValidationError({"detail": "В заказе нет ни одного прибора"})
        order.issued_at = timezone.now() if issued else None
        order.save(update_fields=["issued_at"])
        return Response(WorkOrderSerializer(order).data)


RESULT = {
    VerificationStatus.DRAFT: "в работе",
    VerificationStatus.REJECTED: "в работе",
}


def _initials(full_name: str) -> str:
    """«Стариков Александр Сергеевич» → «Стариков А. С.» — под подпись в акте."""
    last, *rest = full_name.split() or [""]
    return " ".join([last, *(f"{part[0]}." for part in rest if part)])


def _basis(order: WorkOrder) -> str:
    procurement = order.procurement
    if procurement is None:
        return ""
    parts = [f"закупка {procurement.label}"]
    if procurement.contract_number:
        dated = f" от {procurement.contract_date:%d.%m.%Y}" if procurement.contract_date else ""
        parts.append(f"контракт № {procurement.contract_number}{dated}")
    return ", ".join(parts)


def act_context(order: WorkOrder, kind: str) -> dict:
    client = order.client
    rows = []
    verifications = order.verifications.select_related("instrument__si_type").order_by("created_at", "id")
    for index, item in enumerate(verifications, start=1):
        si = item.instrument.si_type
        protocol = getattr(item, "protocol", None)
        if item.status in (VerificationStatus.READY, VerificationStatus.ACCEPTED):
            result = "годен" if item.suitable else "не годен"
        else:
            result = RESULT.get(item.status, "")
        rows.append({
            "n": index,
            "name": si.name,
            "registry": si.registry_number,
            "serial": item.instrument.serial_number,
            "year": item.instrument.manufacture_year or "",
            "result": result,
            "protocol": protocol.full_number if protocol else "",
        })
    moment = order.issued_at if kind == "issue" and order.issued_at else order.received_at or order.created_at
    moment = timezone.localtime(moment)
    return {
        "kind": kind,
        "title": "Акт приёма средств измерений в поверку" if kind == "receive" else "Акт выдачи средств измерений после поверки",
        "number": f"{order.pk}{'-В' if kind == 'issue' else ''}",
        "date": moment.strftime("%d.%m.%Y"),
        "org": {"name": f"ООО {ORG['name']}", "address": ORG["address_actual"]},
        "customer": {
            "name": client.name, "inn": client.inn, "kpp": client.kpp,
            "address": client.legal_address, "phone": client.phone,
        },
        "contact": order.contact,
        "employee": order.assigned_employee.short_name or _initials(order.assigned_employee.full_name),
        "basis": _basis(order),
        "rows": rows,
        "total": len(rows),
        "suitable": sum(1 for row in rows if row["result"] == "годен"),
        "unsuitable": sum(1 for row in rows if row["result"] == "не годен"),
        "note": order.note,
    }


class OrderActView(APIView):
    def get(self, request, pk: int, kind: str):
        if kind not in ("receive", "issue"):
            raise NotFound("Акт бывает приёма (receive) или выдачи (issue)")
        order = _order(request, pk)
        context = act_context(order, kind)
        if not context["rows"]:
            raise ValidationError({"detail": "В заказе нет ни одного прибора"})
        try:
            data = render_pdf(context, template="act_v1.typ")
        except RenderError as exc:
            raise ValidationError({"detail": f"Не удалось сверстать акт: {exc}"}) from exc
        name = f"Акт {'приёма' if kind == 'receive' else 'выдачи'} № {context['number']} — {order.client.name}.pdf"
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
        return response
