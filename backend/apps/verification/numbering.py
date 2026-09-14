"""Сервис нумерации протоколов.

Требование: номера идут строго по времени поверки (verified_at).
Реальность: поверки приходят не по порядку — офлайн-синхронизация, забытая
поверка, исправление задним числом.

Решение — разделить нумерацию на две зоны:

    ┌─ запечатанная зона ──────────────┐┌─ хвост ────────────┐
    │ 0141 … 0147  подписаны/в ФИФ     ││ 0148 … 0152        │
    │ номера неизменяемы               ││ пересчитываются    │
    └──────────────────────────────────┘└────────────────────┘
                                      ▲
                             sealed_high_water

* `assign_numbers` пересчитывает весь хвост по verified_at. Забытая вчерашняя
  поверка встаёт на своё место, остальные сдвигаются — хронология сохраняется
  автоматически, а порядок прихода с устройств не имеет значения.
* `seal` вызывается при подписании: номер становится неизменяемым.
* `publish` — после успешной выгрузки в ФИФ.
* `insert_out_of_sequence` — единственный способ вставить поверку в уже
  запечатанную зону: литерный подномер (0147А), сразу после своего
  хронологического соседа, с обязательной причиной.
* `void` — аннулирование. Номер остаётся занятым: дыр в журнале быть не должно.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from apps.verification.models import (
    NumberingScope,
    Protocol,
    ProtocolStatus,
    Verification,
)

# Подномера для вставки задним числом: 00767/1, 00767/2 …
# Формат не выдуман — так уже помечали протоколы в журнале руками.
MAX_SUBNUMBER = 99


class NumberingError(Exception):
    pass


@dataclass
class AssignResult:
    """Что именно сделал пересчёт — чтобы показать метрологу перед сохранением."""

    scope: NumberingScope
    sealed_high_water: int
    assigned: list[Protocol] = field(default_factory=list)
    renumbered: list[tuple[Protocol, int | None, int]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.assigned or self.renumbered)

    def describe(self) -> list[str]:
        lines = [f"{p.verification.verified_at:%d.%m.%Y %H:%M} → {p.full_number}" for p in self.assigned]
        lines += [
            f"{p.verification.verified_at:%d.%m.%Y %H:%M}: "
            f"{old if old is not None else '—'} → {new}"
            for p, old, new in self.renumbered
        ]
        return lines


def scope_for(verification: Verification, *, create: bool = True) -> NumberingScope:
    """Область нумерации для поверки: серия + поверитель + год."""
    series = verification.family.series
    if series is None:
        raise NumberingError(
            f"У семейства «{verification.family.name}» не указана серия нумерации, "
            "и серии по умолчанию нет."
        )
    year = verification.verified_at.year if series.resets_yearly else None
    lookup = {"series": series, "employee": verification.verifier, "year": year}
    if create:
        scope, _ = NumberingScope.objects.get_or_create(**lookup)
        return scope
    return NumberingScope.objects.get(**lookup)


def sealed_high_water(scope: NumberingScope) -> int:
    """Наибольший номер, который уже нельзя трогать."""
    return (
        Protocol.objects.filter(scope=scope, status__in=ProtocolStatus.sealed())
        .aggregate(high=Max("seq"))["high"]
        or 0
    )


def _tail_queryset(scope: NumberingScope) -> QuerySet[Protocol]:
    """Протоколы, которые ещё можно перенумеровать, в хронологическом порядке."""
    return (
        Protocol.objects.filter(scope=scope)
        .exclude(status__in=ProtocolStatus.sealed())
        .exclude(suffix__gt="")  # подномера привязаны к соседу, их не двигаем
        .select_related("verification", "scope__series", "scope__employee")
        .order_by("verification__verified_at", "verification__created_at", "pk")
    )


@transaction.atomic
def assign_numbers(scope: NumberingScope, *, dry_run: bool = False) -> AssignResult:
    """Присвоить и пересчитать номера незапечатанного хвоста области.

    Идемпотентна: повторный вызов без новых поверок ничего не меняет.
    """
    # Блокировка строки области — два менеджера, нажавшие кнопку одновременно,
    # не получат одинаковых номеров.
    scope = NumberingScope.objects.select_for_update().get(pk=scope.pk)
    high = sealed_high_water(scope)
    result = AssignResult(scope=scope, sealed_high_water=high)

    tail = list(_tail_queryset(scope))
    if not tail:
        return result

    planned: list[tuple[Protocol, int | None, int]] = []
    number = high
    for protocol in tail:
        number += 1
        planned.append((protocol, protocol.seq, number))

    for protocol, old, new in planned:
        if old is None:
            protocol.seq = new
            result.assigned.append(protocol)
        elif old != new:
            protocol.seq = new
            result.renumbered.append((protocol, old, new))

    if dry_run or not result.changed:
        return result

    # Два прохода: сначала освобождаем номера, иначе уникальный индекс
    # (scope, seq, suffix) споткнётся о промежуточное состояние.
    Protocol.objects.filter(pk__in=[p.pk for p, _, _ in planned]).update(seq=None)

    now = timezone.now()
    for protocol, _old, new in planned:
        protocol.seq = new
        if protocol.status == ProtocolStatus.DRAFT:
            protocol.status = ProtocolStatus.NUMBERED
            protocol.numbered_at = now
        protocol.save(update_fields=["seq", "status", "numbered_at"])

    return result


@transaction.atomic
def insert_out_of_sequence(
    verification: Verification, *, reason: str, template=None
) -> Protocol:
    """Вставить поверку в уже запечатанную зону под дробным подномером.

    Находим последний запечатанный протокол, чья поверка не позже нашей,
    и встаём сразу за ним: 00767 → 00767/1. Хронология журнала сохраняется,
    чужие номера не двигаются.
    """
    if not reason:
        raise NumberingError("Для вставки вне очереди нужна причина — она попадёт в журнал.")

    scope = NumberingScope.objects.select_for_update().get(pk=scope_for(verification).pk)

    neighbour = (
        Protocol.objects.filter(
            scope=scope,
            status__in=ProtocolStatus.sealed(),
            verification__verified_at__lte=verification.verified_at,
        )
        .order_by("-verification__verified_at", "-seq", "-suffix")
        .first()
    )
    if neighbour is None:
        raise NumberingError(
            "Перед этой поверкой нет запечатанных протоколов — "
            "обычный пересчёт хвоста поставит её на место сам."
        )

    used = set(
        Protocol.objects.filter(scope=scope, seq=neighbour.seq)
        .exclude(suffix="")
        .values_list("suffix", flat=True)
    )
    suffix = next((f"/{n}" for n in range(1, MAX_SUBNUMBER + 1) if f"/{n}" not in used), None)
    if suffix is None:
        raise NumberingError(f"Подномера для {neighbour.full_number} закончились.")

    return Protocol.objects.create(
        verification=verification,
        scope=scope,
        seq=neighbour.seq,
        suffix=suffix,
        status=ProtocolStatus.NUMBERED,
        numbered_at=timezone.now(),
        out_of_sequence_reason=reason,
        template=template,
    )


@transaction.atomic
def seal(protocol: Protocol, *, signed_by, at=None) -> Protocol:
    """Подписать протокол: номер становится неизменяемым.

    Право подписи проверяется здесь, а не в интерфейсе, — правило должно
    срабатывать одинаково из веб-формы, из синхронизации и из админки.
    """
    protocol = Protocol.objects.select_for_update().get(pk=protocol.pk)
    if protocol.seq is None:
        raise NumberingError("Нельзя подписать протокол без номера.")
    if protocol.is_sealed:
        raise NumberingError(f"Протокол {protocol.full_number} уже запечатан ({protocol.get_status_display()}).")

    verification = protocol.verification
    if not signed_by.can_sign(verification.family, verification.verified_at.date()):
        raise NumberingError(
            f"У {signed_by.full_name} нет действующей аттестации на "
            f"«{verification.family.name}» на {verification.verified_at:%d.%m.%Y}."
        )

    protocol.status = ProtocolStatus.SIGNED
    protocol.signed_by = signed_by
    protocol.signed_at = at or timezone.now()
    protocol.save(update_fields=["status", "signed_by", "signed_at"])
    return protocol


@transaction.atomic
def publish(protocol: Protocol, *, fif_record_number: str, at=None) -> Protocol:
    """Отметить, что сведения приняты ФИФ «Аршин»."""
    protocol = Protocol.objects.select_for_update().get(pk=protocol.pk)
    if protocol.status != ProtocolStatus.SIGNED:
        raise NumberingError("В ФИФ выгружаются только подписанные протоколы.")
    protocol.status = ProtocolStatus.PUBLISHED
    protocol.fif_record_number = fif_record_number
    protocol.published_at = at or timezone.now()
    protocol.save(update_fields=["status", "fif_record_number", "published_at"])
    return protocol


@transaction.atomic
def void(protocol: Protocol, *, reason: str, replacement: Protocol | None = None) -> Protocol:
    """Аннулировать протокол. Номер остаётся занятым — дыр в журнале быть не должно."""
    if not reason:
        raise NumberingError("Нужна причина аннулирования.")
    protocol = Protocol.objects.select_for_update().get(pk=protocol.pk)
    if protocol.seq is None:
        raise NumberingError("Черновик без номера не аннулируют, а удаляют.")
    protocol.status = ProtocolStatus.VOID
    protocol.void_reason = reason
    protocol.save(update_fields=["status", "void_reason"])
    if replacement is not None:
        replacement.supersedes = protocol
        replacement.save(update_fields=["supersedes"])
    return protocol


def chronology_breaks(scope: NumberingScope) -> list[tuple[Protocol, Protocol]]:
    """Пары протоколов, где номер идёт вразрез со временем поверки.

    В норме список пуст. Непустой — это либо вставки вне очереди (тогда у них
    заполнена out_of_sequence_reason), либо ошибка, которую надо разобрать.
    """
    protocols = list(
        Protocol.objects.filter(scope=scope, seq__isnull=False)
        .exclude(status=ProtocolStatus.VOID)
        .select_related("verification")
        .order_by("seq", "suffix")
    )
    breaks = []
    for previous, current in zip(protocols, protocols[1:]):
        if current.verification.verified_at < previous.verification.verified_at:
            breaks.append((previous, current))
    return breaks


def pending_scopes() -> QuerySet[NumberingScope]:
    """Области, где есть что нумеровать, — для экрана нормоконтроля."""
    return NumberingScope.objects.filter(
        Q(protocols__status=ProtocolStatus.DRAFT)
    ).distinct()
