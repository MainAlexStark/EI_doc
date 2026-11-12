"""Прошлые поверки СИ по заводскому номеру — чтобы не вводить прибор заново.

Поверитель вводит номер с корпуса, а система ищет его в двух местах:

* **у себя** — экземпляры СИ, которые мы уже поверяли (тип, год выпуска, владелец, чем кончилось);
* **во ФГИС «Аршин»** — записи о поверках любых организаций (``apps.arshin.vri``).

Нажатие на найденную строку подставляет в форму всё, что известно: тип (свой, если он заведён
по этому номеру Госреестра и модификации), заводской номер как в реестре, год выпуска.

* ``GET /api/instruments/history/?serial=…`` — обе выдачи; сбой ФГИС не ошибка, своя база
  отвечает всегда (``fgis.ok = false`` и причина). ФГИС отвечает секунд пять (запрос на каждый
  год), поэтому экран спрашивает по отдельности: ``source=own`` — сразу, ``source=fgis`` — следом;
* ``GET /api/instruments/history/fgis/<vri_id>/`` — карточка поверки: год выпуска, владелец,
  методика, эталоны.
"""

from __future__ import annotations

from django.db.models import Count, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.arshin.fif import FifUnavailable, _norm
from apps.arshin.vri import VriClient, serial_variants
from apps.catalog.models import SiType
from apps.verification.models import Instrument

MIN_SERIAL = 3


def type_label(si_type: SiType) -> str:
    parts = [si_type.name]
    if si_type.registry_number:
        parts.append(f"№ {si_type.registry_number}")
    parts.append(si_type.manufacturer or "изготовитель не указан")
    return " · ".join(parts)


def type_brief(si_type: SiType) -> dict:
    return {"id": si_type.pk, "name": si_type.name, "label": type_label(si_type),
            "needs_review": si_type.needs_review}


def match_types(mit_number: str, modification: str, notation: str, by_number: dict[str, list[SiType]]) -> dict:
    """Наши типы по номеру Госреестра: ``si_type`` — тот, чьё имя совпало с модификацией.

    Один номер в реестре — это часто несколько наших типов (СВ-15Х и СВ-15Г с разными расходами),
    поэтому без совпадения имени тип сам не подставляется: человек выбирает из ``candidates``.
    """
    candidates = by_number.get(mit_number, [])
    wanted = _norm(modification or notation)
    exact = [t for t in candidates if wanted and _norm(t.name) == wanted]
    return {
        "si_type": type_brief(exact[0]) if len(exact) == 1 else None,
        "candidates": [type_brief(t) for t in candidates],
    }


def types_by_number(numbers: set[str]) -> dict[str, list[SiType]]:
    grouped: dict[str, list[SiType]] = {}
    if numbers:
        for si_type in SiType.objects.filter(registry_number__in=numbers, is_active=True).order_by("name"):
            grouped.setdefault(si_type.registry_number, []).append(si_type)
    return grouped


def own_history(serial: str) -> list[dict]:
    """Наши экземпляры с таким номером (в любой раскладке похожих букв) и их последняя поверка."""
    query = Q()
    for variant in serial_variants(serial):
        query |= Q(serial_number__iexact=variant)
    instruments = (
        Instrument.objects.filter(query)
        .select_related("si_type", "owner", "site")
        .annotate(verifications_count=Count("verifications"))
        .order_by("-pk")[:10]
    )
    rows = []
    for instrument in instruments:
        last = (
            instrument.verifications.select_related("protocol", "verifier")
            .order_by("-verified_at").first()
        )
        protocol = getattr(last, "protocol", None) if last else None
        rows.append({
            "instrument_id": instrument.pk,
            "serial_number": instrument.serial_number,
            "manufacture_year": instrument.manufacture_year,
            "si_type": type_brief(instrument.si_type),
            "registry_number": instrument.si_type.registry_number,
            "owner": instrument.owner.name if instrument.owner else "",
            "site": instrument.site.address if instrument.site else "",
            "verifications_count": instrument.verifications_count,
            "last": None if last is None else {
                "verification_id": last.pk,
                "verified_at": last.verified_at.isoformat() if last.verified_at else None,
                "next_verification_date": (
                    last.next_verification_date.isoformat() if last.next_verification_date else None
                ),
                "suitable": last.suitable,
                "status": last.get_status_display(),
                "verifier": str(last.verifier) if last.verifier_id else "",
                "protocol_number": protocol.full_number if protocol else "",
            },
        })
    return rows


class InstrumentHistoryView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("serial", str, description="Заводской номер с корпуса"),
            OpenApiParameter("source", str, description="own — только своя база, fgis — только ФГИС, иначе обе"),
        ],
        responses={200: dict},
    )
    def get(self, request):
        serial = (request.query_params.get("serial") or "").strip()
        if len(serial) < MIN_SERIAL:
            return Response({"serial": serial, "own": [],
                             "fgis": {"ok": True, "error": "", "items": [], "partial": False, "years": []}})

        source = request.query_params.get("source") or "all"
        body: dict = {"serial": serial}
        if source != "fgis":
            body["own"] = own_history(serial)
        if source != "own":
            body["fgis"] = self._fgis(serial)
        return Response(body)

    @staticmethod
    def _fgis(serial: str) -> dict:
        try:
            history = VriClient().history(serial)
        except FifUnavailable as exc:
            return {"ok": False, "error": str(exc), "items": [], "partial": False, "years": []}
        fgis = {"ok": True, **history.as_dict()}
        if history.partial:
            fgis["error"] = (
                "ФГИС ответила не за все годы (нет "
                + ", ".join(str(y) for y in sorted(history.failed_years)) + ")"
            )
        grouped = types_by_number({item["mit_number"] for item in fgis["items"] if item["mit_number"]})
        for item in fgis["items"]:
            item.update(match_types(item["mit_number"], item["modification"], item["mit_notation"], grouped))
        return fgis


class FgisVerificationCardView(APIView):
    @extend_schema(responses={200: dict})
    def get(self, request, vri_id: str):
        try:
            card = VriClient().card(vri_id)
        except FifUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        body = card.as_dict()
        body.update(match_types(card.mit_number, card.modification, card.mit_notation,
                                types_by_number({card.mit_number} if card.mit_number else set())))
        return Response(body)
