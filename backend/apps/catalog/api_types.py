"""Справочник типов СИ без админки.

Раньше новый тип счётчика заводился только через Django-админку, причём
метрологические характеристики вводились руками в JSON-поле. Для обычного
поверителя это непреодолимо. Теперь:

* поверитель на экране поверок находит тип в ФИФ (или вводит вручную, если
  реестр недоступен), выбирает шаблон характеристик по Ду — значения
  подставляются, их можно поправить — и сразу продолжает работу;
* такой тип помечен ``needs_review``: поверки с ним вносятся и считаются как
  обычно, но протокол не подпишется, пока метролог не проверит тип
  (``apps.verification.numbering.seal``);
* метролог видит очередь на вкладке «Типы СИ», правит и подтверждает.

Шаблоны по Ду не придуманы: это наборы характеристик, которые уже
встречаются у проверенных типов в справочнике (60 типов из рабочих шаблонов
старого приложения — ``manage.py seed_catalog``).
"""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Role

WATER_CALCULATOR = "water_meter"

# Порядок важен — в нём поля показываются на форме.
LIMIT_FIELDS: "OrderedDict[str, str]" = OrderedDict(
    [
        ("q_min", "Q наименьший, м³/ч"),
        ("q_transition_b", "Q переходный, класс В, м³/ч"),
        ("q_transition_a", "Q переходный, класс А, м³/ч"),
        ("q_nominal", "Q номинальный, м³/ч"),
        ("q_max", "Q наибольший, м³/ч"),
        ("error_below_transition", "Погрешность ниже Q переходного, ±%"),
        ("error_above_transition", "Погрешность от Q переходного и выше, ±%"),
    ]
)

# Q номинальный → условный проход. Только подпись к шаблону, на расчёт не влияет.
NOMINAL_TO_DN = {
    Decimal("1.5"): 15,
    Decimal("2.5"): 20,
    Decimal("3.5"): 25,
    Decimal("6"): 32,
    Decimal("10"): 40,
    Decimal("15"): 50,
}

REVIEWER_ROLES = {Role.OWNER, Role.HEAD, Role.METROLOGIST}
CREATOR_ROLES = REVIEWER_ROLES | {Role.MANAGER, Role.VERIFIER}

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "water_meter_types.json"


def is_reviewer(user) -> bool:
    return bool(user.is_superuser or getattr(user, "role", None) in REVIEWER_ROLES)


def can_create(user) -> bool:
    return bool(user.is_superuser or getattr(user, "role", None) in CREATOR_ROLES)


def _number(value) -> Decimal:
    return Decimal(str(value).strip().replace(",", "."))


def clean_water_limits(raw: dict) -> dict:
    """Проверить характеристики счётчика воды и привести к числам.

    Сообщения — для поверителя на объекте, а не для разработчика.
    """
    if not isinstance(raw, dict):
        raise serializers.ValidationError("Характеристики должны быть набором полей")

    values: dict[str, Decimal] = {}
    problems: list[str] = []
    for key, label in LIMIT_FIELDS.items():
        value = raw.get(key)
        if value in (None, ""):
            problems.append(f"не заполнено «{label}»")
            continue
        try:
            number = _number(value)
        except (InvalidOperation, ValueError):
            problems.append(f"«{label}»: не число")
            continue
        if number <= 0:
            problems.append(f"«{label}» должно быть больше нуля")
            continue
        values[key] = number
    if problems:
        text = "; ".join(problems)
        raise serializers.ValidationError(text[:1].upper() + text[1:])

    order = ["q_min", "q_transition_b", "q_nominal", "q_max"]
    for lower, upper in zip(order, order[1:]):
        if not values[lower] < values[upper]:
            raise serializers.ValidationError(
                f"«{LIMIT_FIELDS[lower]}» должен быть меньше, чем «{LIMIT_FIELDS[upper]}»"
            )
    if not values["q_transition_b"] <= values["q_transition_a"] < values["q_nominal"]:
        raise serializers.ValidationError(
            "Q переходный класса А должен быть не меньше, чем у класса В, и меньше Q номинального"
        )
    if values["error_below_transition"] < values["error_above_transition"]:
        raise serializers.ValidationError(
            "Погрешность ниже Q переходного обычно больше, чем выше него — проверьте, не перепутаны ли поля"
        )

    # Храним как числа — так же, как их кладёт seed_catalog.
    return {key: float(values[key]) for key in LIMIT_FIELDS}


def default_family() -> MeasurementFamily | None:
    return (
        MeasurementFamily.objects.filter(calculator_key=WATER_CALCULATOR).order_by("pk").first()
        or MeasurementFamily.objects.order_by("pk").first()
    )


# ---------------------------------------------------------------------------
# Сериализаторы
# ---------------------------------------------------------------------------
def _user_name(user) -> str:
    if user is None:
        return ""
    employee = getattr(user, "employee", None)
    return employee.full_name if employee else user.email


class SiTypeSerializer(serializers.ModelSerializer):
    family_name = serializers.CharField(source="family.name", read_only=True)
    calculator_key = serializers.CharField(source="family.calculator_key", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()

    class Meta:
        model = SiType
        fields = [
            "id", "family", "family_name", "calculator_key", "registry_number", "name",
            "manufacturer", "verification_interval_months", "limits", "fif_url",
            "fif_uuid", "type_title", "certificate_valid_to",
            "needs_review", "created_by_name", "reviewed_by_name", "reviewed_at", "label",
        ]
        read_only_fields = ["needs_review", "reviewed_at"]

    def get_created_by_name(self, obj: SiType) -> str:
        return _user_name(obj.created_by)

    def get_reviewed_by_name(self, obj: SiType) -> str:
        return _user_name(obj.reviewed_by)

    def get_label(self, obj: SiType) -> str:
        parts = [obj.name]
        if obj.registry_number:
            parts.append(f"№ {obj.registry_number}")
        parts.append(obj.manufacturer or "изготовитель не указан")
        return " · ".join(parts)


class SiTypeWriteSerializer(serializers.Serializer):
    family = serializers.PrimaryKeyRelatedField(
        queryset=MeasurementFamily.objects.all(), required=False, allow_null=True
    )
    registry_number = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=200)
    manufacturer = serializers.CharField(max_length=200, allow_blank=True, required=False, default="")
    verification_interval_months = serializers.IntegerField(
        min_value=1, max_value=240, required=False, allow_null=True
    )
    limits = serializers.JSONField(required=False)
    fif_url = serializers.URLField(required=False, allow_blank=True, default="")
    fif_raw = serializers.JSONField(required=False)
    fif_uuid = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    type_title = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    certificate_valid_to = serializers.DateField(required=False, allow_null=True)

    def validate_registry_number(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Укажите номер в Госреестре")
        return value

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Укажите наименование")
        return value

    def validate(self, attrs):
        family = attrs.get("family") or (self.instance.family if self.instance else default_family())
        if family is None:
            raise serializers.ValidationError("В справочнике нет ни одного семейства СИ")
        attrs["family"] = family
        if family.calculator_key == WATER_CALCULATOR:
            if "limits" in attrs or self.instance is None:
                attrs["limits"] = clean_water_limits(attrs.get("limits") or {})
        return attrs


# ---------------------------------------------------------------------------
# Вьюхи
# ---------------------------------------------------------------------------
class SiTypeListCreateView(APIView):
    """GET — справочник (для офлайн-поиска и вкладки «Типы СИ»), POST — завести тип."""

    @extend_schema(
        parameters=[
            OpenApiParameter("needs_review", bool, description="Только ждущие проверки"),
            OpenApiParameter("q", str, description="Поиск по номеру, наименованию, изготовителю"),
        ],
        responses=SiTypeSerializer(many=True),
    )
    def get(self, request):
        qs = SiType.objects.filter(is_active=True).select_related(
            "family", "created_by__employee", "reviewed_by__employee"
        )
        if request.query_params.get("needs_review") in ("1", "true", "yes"):
            qs = qs.filter(needs_review=True)
        query = (request.query_params.get("q") or "").strip()
        if query:
            qs = qs.filter(
                Q(registry_number__icontains=query)
                | Q(name__icontains=query)
                | Q(manufacturer__icontains=query)
            )
        qs = qs.order_by("-needs_review", "name", "registry_number")[:1000]
        return Response(SiTypeSerializer(qs, many=True).data)

    @extend_schema(request=SiTypeWriteSerializer, responses={201: SiTypeSerializer, 200: SiTypeSerializer})
    def post(self, request):
        if not can_create(request.user):
            raise PermissionDenied("Заводить типы СИ может поверитель, метролог или руководитель")
        payload = SiTypeWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        # Тот же тип уже есть — просто вернуть его: двое поверителей могли
        # завести один и тот же счётчик почти одновременно.
        existing = SiType.objects.filter(
            registry_number=data["registry_number"], name=data["name"]
        ).first()
        if existing is not None:
            if not existing.is_active:
                existing.is_active = True
                existing.save(update_fields=["is_active"])
            body = SiTypeSerializer(existing).data
            body["existing"] = True
            return Response(body, status=status.HTTP_200_OK)

        reviewer = is_reviewer(request.user)
        fields = dict(
            family=data["family"],
            registry_number=data["registry_number"],
            name=data["name"],
            manufacturer=data.get("manufacturer", "").strip(),
            limits=data.get("limits") or {},
            fif_url=data.get("fif_url", ""),
            fif_raw=data.get("fif_raw") or {},
            fif_uuid=data.get("fif_uuid", "").strip(),
            type_title=data.get("type_title", "").strip(),
            certificate_valid_to=data.get("certificate_valid_to"),
            created_by=request.user,
            needs_review=not reviewer,
            reviewed_by=request.user if reviewer else None,
            reviewed_at=timezone.now() if reviewer else None,
        )
        if data.get("verification_interval_months"):
            fields["verification_interval_months"] = data["verification_interval_months"]
        try:
            with transaction.atomic():
                si_type = SiType.objects.create(**fields)
        except IntegrityError:
            si_type = SiType.objects.get(registry_number=data["registry_number"], name=data["name"])
            body = SiTypeSerializer(si_type).data
            body["existing"] = True
            return Response(body, status=status.HTTP_200_OK)
        if si_type.fif_uuid:
            from apps.catalog.api_fif import schedule_documents

            transaction.on_commit(lambda: schedule_documents(si_type.fif_uuid))
        return Response(SiTypeSerializer(si_type).data, status=status.HTTP_201_CREATED)


class SiTypeDetailView(APIView):
    """PATCH — поправить тип. Метролог — любой; автор — свой, пока он не проверен."""

    @extend_schema(request=SiTypeWriteSerializer, responses=SiTypeSerializer)
    def patch(self, request, pk: int):
        si_type = get_object_or_404(SiType, pk=pk)
        own_unreviewed = si_type.needs_review and si_type.created_by_id == request.user.pk
        if not (is_reviewer(request.user) or own_unreviewed):
            raise PermissionDenied("Проверенный тип может править только метролог")

        payload = SiTypeWriteSerializer(instance=si_type, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        for key, value in payload.validated_data.items():
            if key == "manufacturer":
                value = value.strip()
            if key == "verification_interval_months" and not value:
                continue
            setattr(si_type, key, value)
        try:
            si_type.save()
        except IntegrityError:
            return Response(
                {"detail": "Тип с таким номером в Госреестре и наименованием уже есть"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(SiTypeSerializer(si_type).data)


class SiTypeConfirmView(APIView):
    """POST — метролог проверил характеристики, протоколы с этим типом можно подписывать."""

    @extend_schema(request=None, responses=SiTypeSerializer)
    def post(self, request, pk: int):
        if not is_reviewer(request.user):
            raise PermissionDenied("Подтверждать типы СИ может только метролог или руководитель")
        si_type = get_object_or_404(SiType, pk=pk)
        if si_type.family.calculator_key == WATER_CALCULATOR:
            try:
                clean_water_limits(si_type.limits or {})
            except serializers.ValidationError as exc:
                return Response(
                    {"detail": f"Сначала исправьте характеристики: {' '.join(exc.detail)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        si_type.needs_review = False
        si_type.reviewed_by = request.user
        si_type.reviewed_at = timezone.now()
        si_type.save(update_fields=["needs_review", "reviewed_by", "reviewed_at"])
        return Response(SiTypeSerializer(si_type).data)


def _limits_key(limits: dict) -> tuple | None:
    try:
        return tuple(str(_number(limits[key]).normalize()) for key in LIMIT_FIELDS)
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return None


def build_presets() -> list[dict]:
    """Наборы характеристик, которые уже встречаются у проверенных типов."""
    counter: Counter = Counter()
    examples: dict[tuple, list[str]] = {}
    sample: dict[tuple, dict] = {}

    rows = list(
        SiType.objects.filter(
            is_active=True, needs_review=False, family__calculator_key=WATER_CALCULATOR
        ).values_list("name", "limits")
    )
    if not rows and FIXTURE.exists():
        rows = [(item["name"], item["limits"]) for item in json.loads(FIXTURE.read_text(encoding="utf-8"))]

    for name, limits in rows:
        key = _limits_key(limits or {})
        if key is None:
            continue
        counter[key] += 1
        sample.setdefault(key, {k: float(_number(limits[k])) for k in LIMIT_FIELDS})
        names = examples.setdefault(key, [])
        if name not in names and len(names) < 4:
            names.append(name)

    presets = []
    for key, count in counter.most_common():
        limits = sample[key]
        dn = NOMINAL_TO_DN.get(_number(limits["q_nominal"]).normalize())
        error_text = f"±{_fmt(limits['error_below_transition'])} / ±{_fmt(limits['error_above_transition'])} %"
        presets.append({
            "key": "|".join(key),
            "dn": dn,
            "label": f"Ду{dn}" if dn else f"Qn {_fmt(limits['q_nominal'])} м³/ч",
            "detail": f"Qn {_fmt(limits['q_nominal'])}, Qmax {_fmt(limits['q_max'])} м³/ч, погрешность {error_text}",
            "count": count,
            "examples": examples[key],
            "limits": limits,
        })
    return presets


def _fmt(value: float) -> str:
    return f"{Decimal(str(value)).normalize():f}".replace(".", ",")


class SiTypePresetsView(APIView):
    """GET — шаблоны характеристик по Ду и подписи полей для формы нового типа."""

    @extend_schema(responses={200: dict})
    def get(self, request):
        families = MeasurementFamily.objects.exclude(calculator_key="").order_by("name")
        family = default_family()
        return Response({
            "fields": [{"key": key, "label": label} for key, label in LIMIT_FIELDS.items()],
            "presets": build_presets(),
            "families": [
                {"id": f.pk, "name": f.name, "calculator_key": f.calculator_key} for f in families
            ],
            "default_family": family.pk if family else None,
            "can_review": is_reviewer(request.user),
        })
