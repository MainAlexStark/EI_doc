"""Отправка результатов поверки в ФГИС «Аршин».

Путь протокола: подписан → попал в пакет (XML) → пакет загружен в ЛК ФИФ →
сверка с открытым реестром нашла запись → протокол «Опубликован в ФИФ» с
номером записи.

Почему не прямая запись по API: запись в ФИФ для поверителей идёт пакетным
XML через личный кабинет (для аккредитованных лиц часть — через ФГИС
Росаккредитации с УКЭП). Поэтому система готовит и проверяет пакет, а
загружает его человек; всё, что после, — снова автоматически.

Формат XML — схема «Загрузка сведений о результатах поверки» ЛК ФИФ ОЕИ.
Пространство имён и предел строк в файле вынесены в настройки
(ARSHIN_XML_NAMESPACE, ARSHIN_BATCH_LIMIT): Росстандарт меняет версии схем,
и перед боевой загрузкой файл нужно сверить с действующей XSD из ЛК — см.
docs/arshin.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.arshin.models import ArshinSubmission, ArshinSubmissionItem, ItemStatus, SubmissionStatus
from apps.verification.models import Protocol, ProtocolStatus, Verification

log = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "urn://fgis-arshin.gost.ru/module-verifications/import/2020-06-19"

# Виды поверки в схеме ФИФ.
VRF_PRIMARY = "1"
VRF_PERIODIC = "2"


class SubmissionError(ValueError):
    """Пакет собрать нельзя — сообщение для человека."""


def namespace() -> str:
    return getattr(settings, "ARSHIN_XML_NAMESPACE", "") or DEFAULT_NAMESPACE


def batch_limit() -> int:
    return int(getattr(settings, "ARSHIN_BATCH_LIMIT", 0) or 1000)


def sign_cipher() -> str:
    return (getattr(settings, "ARSHIN_SIGN_CIPHER", "") or "").strip()


# ---------------------------------------------------------------------------
# Готовность протокола
# ---------------------------------------------------------------------------
def problems(protocol: Protocol) -> list[str]:
    """Чего не хватает протоколу, чтобы ФИФ его принял. Пусто — готов."""
    found: list[str] = []
    verification = protocol.verification
    instrument = verification.instrument
    si_type = instrument.si_type

    if protocol.status != ProtocolStatus.SIGNED:
        found.append(f"протокол не подписан (состояние: {protocol.get_status_display().lower()})")
    if not sign_cipher():
        found.append("не задан шифр знака поверки организации (ARSHIN_SIGN_CIPHER)")
    if not (si_type.registry_number or "").strip():
        found.append("у типа СИ нет номера в Госреестре")
    if not (instrument.serial_number or "").strip():
        found.append("нет заводского номера")
    if not verification.method_id:
        found.append("не указана методика поверки")
    if not verification.standards.exists():
        found.append("не указаны эталоны")
    if verification.suitable and not verification.next_verification_date:
        found.append("годен, но нет даты следующей поверки")
    if not verification.suitable and not (verification.unsuitability_reason or "").strip():
        found.append("непригоден, но не указаны причины")
    return found


def vrf_type(verification: Verification) -> str:
    """Первичная или периодическая: явная пометка, иначе по истории экземпляра."""
    explicit = str((verification.results or {}).get("verification_kind", "")).lower()
    if explicit in {"primary", "первичная", VRF_PRIMARY}:
        return VRF_PRIMARY
    if explicit in {"periodic", "периодическая", VRF_PERIODIC}:
        return VRF_PERIODIC
    earlier = Verification.objects.filter(
        instrument=verification.instrument, verified_at__lt=verification.verified_at
    ).exists()
    if earlier:
        return VRF_PERIODIC
    return VRF_PRIMARY if getattr(settings, "ARSHIN_DEFAULT_PRIMARY", False) else VRF_PERIODIC


def eligible_protocols() -> QuerySet[Protocol]:
    """Подписанные протоколы, которые ещё не в действующем пакете."""
    busy = ArshinSubmissionItem.objects.filter(status__in=ItemStatus.active()).values("protocol_id")
    return (
        Protocol.objects.filter(status=ProtocolStatus.SIGNED)
        .exclude(pk__in=busy)
        .select_related(
            "verification__instrument__si_type", "verification__instrument__owner",
            "verification__verifier", "verification__method", "scope__series", "scope__employee",
        )
        .prefetch_related("verification__standards")
        .order_by("verification__verified_at", "pk")
    )


# ---------------------------------------------------------------------------
# Пакет
# ---------------------------------------------------------------------------
@dataclass
class BuildResult:
    submission: ArshinSubmission | None
    refused: list[dict] = field(default_factory=list)


@transaction.atomic
def create_submission(protocol_ids: list[int] | None = None, *, user=None) -> BuildResult:
    """Собрать пакет из указанных протоколов (или из всех готовых).

    Неготовые протоколы в пакет не попадают и возвращаются с причинами —
    пакет из-за одного протокола не падает.
    """
    candidates = eligible_protocols().select_for_update(of=("self",))
    if protocol_ids is not None:
        candidates = candidates.filter(pk__in=protocol_ids)
    candidates = list(candidates[: batch_limit()])

    refused: list[dict] = []
    if protocol_ids is not None:
        found = {p.pk for p in candidates}
        for pk in protocol_ids:
            if pk not in found:
                refused.append({"protocol": pk, "number": "",
                                "problems": ["не подписан или уже отправлен в другом пакете"]})

    ready: list[Protocol] = []
    for protocol in candidates:
        issues = problems(protocol)
        if issues:
            refused.append({"protocol": protocol.pk, "number": protocol.full_number, "problems": issues})
        else:
            ready.append(protocol)

    if not ready:
        return BuildResult(submission=None, refused=refused)

    submission = ArshinSubmission.objects.create(created_by=user if getattr(user, "pk", None) else None)
    ArshinSubmissionItem.objects.bulk_create(
        [ArshinSubmissionItem(submission=submission, protocol=p) for p in ready]
    )
    write_xml(submission)
    return BuildResult(submission=submission, refused=refused)


def _el(parent: ET.Element, tag: str, text: object | None = None) -> ET.Element:
    node = ET.SubElement(parent, f"{{{namespace()}}}{tag}")
    if text is not None:
        node.text = str(text)
    return node


def _decimal(value) -> str:
    return f"{value}".replace(".", ",") if value is not None else ""


def result_element(parent: ET.Element, protocol: Protocol) -> ET.Element:
    """Одна запись <result> о поверке."""
    verification = protocol.verification
    instrument = verification.instrument
    si_type = instrument.si_type

    result = _el(parent, "result")
    mi_info = _el(result, "miInfo")
    single = _el(mi_info, "singleMI")
    _el(single, "mitypeNumber", si_type.registry_number.strip())
    _el(single, "manufactureNum", instrument.serial_number.strip())
    if instrument.manufacture_year:
        _el(single, "manufactureYear", instrument.manufacture_year)
    _el(single, "modification", si_type.name.strip())

    _el(result, "signCipher", sign_cipher())
    _el(result, "miOwner", str(instrument.owner) if instrument.owner_id else "Физическое лицо")
    _el(result, "vrfDate", verification.verified_at.astimezone(timezone.get_current_timezone()).date().isoformat())
    if verification.suitable and verification.next_verification_date:
        _el(result, "validDate", verification.next_verification_date.isoformat())
    _el(result, "type", vrf_type(verification))
    _el(result, "calibration", "false")

    if verification.suitable:
        applicable = _el(result, "applicable")
        _el(applicable, "signPass", "false")
        _el(applicable, "signMi", "false")
    else:
        inapplicable = _el(result, "inapplicable")
        _el(inapplicable, "reasons", verification.unsuitability_reason.strip()[:1000])

    _el(result, "docTitle", str(verification.method.designation))
    _el(result, "metrologist", verification.verifier.full_name)

    means = _el(result, "means")
    for standard in verification.standards.all():
        mieta = _el(means, "mieta")
        _el(mieta, "number", standard.fif_number)

    conditions = _el(result, "conditions")
    if verification.temperature is not None:
        _el(conditions, "temperature", f"{_decimal(verification.temperature)} °C")
    if verification.pressure is not None:
        _el(conditions, "pressure", f"{_decimal(verification.pressure)} кПа")
    if verification.humidity is not None:
        _el(conditions, "hymidity", f"{_decimal(verification.humidity)} %")

    _el(result, "additional_info", f"Протокол {protocol.full_number}")
    return result


def render_xml(protocols: list[Protocol]) -> bytes:
    ET.register_namespace("gost", namespace())
    root = ET.Element(f"{{{namespace()}}}application")
    for protocol in protocols:
        result_element(root, protocol)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_xml(submission: ArshinSubmission) -> bytes:
    protocols = [
        item.protocol
        for item in submission.items.select_related(
            "protocol__verification__instrument__si_type", "protocol__verification__instrument__owner",
            "protocol__verification__verifier", "protocol__verification__method",
            "protocol__scope__series", "protocol__scope__employee",
        ).prefetch_related("protocol__verification__standards")
    ]
    payload = render_xml(protocols)
    submission.sha256 = hashlib.sha256(payload).hexdigest()
    submission.xml.save(f"arshin-{submission.pk:05d}.xml", ContentFile(payload), save=False)
    submission.save(update_fields=["xml", "sha256"])
    return payload


# ---------------------------------------------------------------------------
# После загрузки
# ---------------------------------------------------------------------------
@transaction.atomic
def mark_uploaded(submission: ArshinSubmission) -> ArshinSubmission:
    if submission.status != SubmissionStatus.GENERATED:
        raise SubmissionError("Отметить загрузку можно только у сформированного пакета")
    submission.status = SubmissionStatus.UPLOADED
    submission.uploaded_at = timezone.now()
    submission.save(update_fields=["status", "uploaded_at"])
    submission.items.filter(status=ItemStatus.PENDING).update(status=ItemStatus.SENT)
    return submission


@transaction.atomic
def cancel(submission: ArshinSubmission) -> ArshinSubmission:
    """Пакет не загружали — отпустить протоколы обратно в очередь."""
    if submission.status != SubmissionStatus.GENERATED:
        raise SubmissionError("Отменить можно только ещё не загруженный пакет")
    submission.status = SubmissionStatus.CANCELLED
    submission.save(update_fields=["status"])
    submission.items.filter(status=ItemStatus.PENDING).update(
        status=ItemStatus.REJECTED, error="пакет отменён до загрузки"
    )
    return submission


RECORD_RE = re.compile(r"^\d-\d{5,}$")


@transaction.atomic
def accept(item: ArshinSubmissionItem, record_number: str) -> ArshinSubmissionItem:
    """ФИФ присвоил записи номер — протокол опубликован."""
    record_number = (record_number or "").strip()
    if not record_number:
        raise SubmissionError("Нужен номер записи в ФИФ")
    item.status = ItemStatus.ACCEPTED
    item.fif_record_number = record_number
    item.error = ""
    item.save(update_fields=["status", "fif_record_number", "error", "updated_at"])

    protocol = Protocol.objects.select_for_update().get(pk=item.protocol_id)
    protocol.status = ProtocolStatus.PUBLISHED
    protocol.fif_record_number = record_number
    protocol.published_at = protocol.published_at or timezone.now()
    protocol.save(update_fields=["status", "fif_record_number", "published_at"])
    _finish_if_complete(item.submission)
    return item


@transaction.atomic
def reject(item: ArshinSubmissionItem, reason: str) -> ArshinSubmissionItem:
    """ФИФ запись не принял — протокол возвращается в очередь на следующий пакет."""
    item.status = ItemStatus.REJECTED
    item.error = (reason or "").strip() or "отклонено ФИФ"
    item.save(update_fields=["status", "error", "updated_at"])
    _finish_if_complete(item.submission)
    return item


def _finish_if_complete(submission: ArshinSubmission) -> None:
    open_items = submission.items.filter(status__in=[ItemStatus.PENDING, ItemStatus.SENT]).exists()
    if not open_items and submission.status == SubmissionStatus.UPLOADED:
        submission.status = SubmissionStatus.DONE
        submission.save(update_fields=["status"])


# ---------------------------------------------------------------------------
# Сверка с открытым реестром
# ---------------------------------------------------------------------------
def _matches(row: dict, verification: Verification) -> bool:
    serial = str(row.get("mi_number") or row.get("mi.number") or "").strip().lower()
    if serial != verification.instrument.serial_number.strip().lower():
        return False
    when = str(row.get("verification_date") or "")
    local = verification.verified_at.astimezone(timezone.get_current_timezone()).date()
    return when[:10] in {local.isoformat(), local.strftime("%d.%m.%Y")}


def find_record(verification: Verification, client=None) -> str:
    """Номер записи в ФИФ для этой поверки или пусто, если ещё не опубликована."""
    from apps.arshin.fif import FifClient

    client = client or FifClient()
    instrument = verification.instrument
    local = verification.verified_at.astimezone(timezone.get_current_timezone()).date()
    params = {
        "mit_number": instrument.si_type.registry_number,
        "mi_number": instrument.serial_number,
        "verification_date": local.strftime("%d.%m.%Y"),
        "rows": 20,
    }
    cipher = sign_cipher()
    for row in client.search_vri(params):
        if cipher and str(row.get("mi.signCipher") or row.get("sign_cipher") or cipher) != cipher:
            continue
        if _matches(row, verification):
            return str(row.get("vri_id") or row.get("result_docnum") or "").strip()
    return ""


def reconcile(submission: ArshinSubmission, client=None) -> dict:
    """Пройти по отправленным строкам и забрать номера опубликованных записей."""
    accepted = 0
    waiting = 0
    for item in submission.items.filter(status=ItemStatus.SENT).select_related(
        "protocol__verification__instrument__si_type"
    ):
        try:
            number = find_record(item.protocol.verification, client=client)
        except Exception as exc:  # ФГИС недоступна — попробуем в следующий раз
            log.warning("Сверка с ФИФ не удалась: %s", exc)
            waiting += 1
            continue
        if number:
            accept(item, number)
            accepted += 1
        else:
            waiting += 1
    submission.reconciled_at = timezone.now()
    submission.attempts += 1
    submission.save(update_fields=["reconciled_at", "attempts"])
    return {"accepted": accepted, "waiting": waiting}


def reconcile_pending(max_age_days: int = 30, client=None) -> int:
    """Сверить все загруженные пакеты не старше max_age_days. Для Celery beat."""
    since = timezone.now() - dt.timedelta(days=max_age_days)
    total = 0
    for submission in ArshinSubmission.objects.filter(
        status=SubmissionStatus.UPLOADED, uploaded_at__gte=since
    ):
        total += reconcile(submission, client=client)["accepted"]
    return total
