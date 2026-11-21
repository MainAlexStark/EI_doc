"""Операции над командировкой — одни и те же для API, админки и ассистента."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.core.models import Employee
from apps.hub import routing
from apps.logistics import geocode
from apps.logistics.models import (
    UNFINISHED,
    PassStatus,
    Procurement,
    StopPurpose,
    ProcurementItem,
    StopStatus,
    Trip,
    TripKind,
    TripStatus,
    TripStop,
)
from apps.verification.models import (
    Client,
    ClientKind,
    Site,
    Verification,
    VerificationStatus,
    WorkOrder,
    WorkOrderKind,
    WorkOrderStatus,
)

#: Частник, который не назвался: в журнале владельцем так и пишется.
ANONYMOUS_CLIENT = "Физическое лицо"


class TripError(ValueError):
    """Операция невозможна по смыслу — текст показывается человеку как есть."""


@dataclass
class StopInput:
    address: str = ""
    site_id: int | None = None
    client_name: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    latitude: float | None = None
    longitude: float | None = None
    postal_code: str = ""
    fias_id: str = ""
    district: str = ""
    planned_date: dt.date | None = None
    planned_count: int | None = None
    note: str = ""
    is_restricted: bool = False
    access_note: str = ""
    added_on_site: bool = False
    locate: bool = True  # искать координаты, если их не прислали
    purpose: str = StopPurpose.VISIT


def _coord(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(float(value), 6)))


def _address_key(address: str) -> str:
    return " ".join(address.split()).casefold()


def _client_for(trip: Trip, data: StopInput) -> Client:
    if trip.procurement_id:
        return trip.procurement.customer
    name = data.client_name.strip()
    if not name:
        client, _ = Client.objects.get_or_create(kind=ClientKind.PERSON, name=ANONYMOUS_CLIENT, phone="")
        return client
    phone = data.contact_phone.strip()
    existing = Client.objects.filter(name__iexact=name, phone=phone).first() if phone else None
    return existing or Client.objects.create(kind=ClientKind.PERSON, name=name, phone=phone)


def _site_for(client: Client, data: StopInput, hint: str = "") -> Site:
    if data.site_id:
        return Site.objects.get(pk=data.site_id)
    address = " ".join(data.address.split())
    if not address:
        raise TripError("Укажите адрес")
    site = Site.objects.filter(client=client, address__iexact=address).first()
    lat, lon = data.latitude, data.longitude
    if site is None:
        if lat is None and data.locate:
            point = geocode.geocode(address, hint)
            if point:
                lat, lon = point
        return Site.objects.create(
            client=client,
            address=address,
            postal_code=data.postal_code[:6],
            fias_id=data.fias_id,
            district=routing.resolve_district(data.district),
            latitude=_coord(lat),
            longitude=_coord(lon),
            is_restricted=data.is_restricted,
            access_note=data.access_note,
            contact=", ".join(filter(None, [data.contact_name.strip(), data.contact_phone.strip()]))[:160],
        )
    # Знакомый адрес: точку с телефона считаем точнее той, что была.
    if lat is not None and lon is not None:
        site.latitude, site.longitude = _coord(lat), _coord(lon)
        site.save(update_fields=["latitude", "longitude"])
    return site


@transaction.atomic
def add_stop(trip: Trip, data: StopInput, *, employee: Employee | None = None,
             client: Client | None = None) -> TripStop:
    """Завести адрес и наряд под него. ``employee`` — кто будет поверять (по умолчанию старший).

    ``client`` — заказчик, если он уже известен (забор приборов у юрлица); иначе — по закупке
    командировки или по имени из адреса.
    """
    if not trip.is_open:
        raise TripError("Командировка закрыта — адреса больше не добавляются")
    # Один адрес — одна строка командировки, как бы его ни набрали (регистр, пробелы).
    taken = {
        _address_key(stop.site.address): stop.site_id
        for stop in trip.stops.exclude(status=StopStatus.REFUSED).select_related("site")
    }
    if data.site_id in taken.values() or (data.address and _address_key(data.address) in taken):
        raise TripError(f"Адрес уже есть в командировке: {' '.join(data.address.split()) or 'этот объект'}")
    client = client or _client_for(trip, data)
    site = _site_for(client, data, trip.destination)

    planned_date = data.planned_date or (timezone.localdate() if data.added_on_site else trip.date_start)
    purpose = data.purpose if data.purpose in StopPurpose.values else StopPurpose.VISIT
    note = f"Командировка: {trip.title}"
    if purpose != StopPurpose.VISIT:
        note = f"{StopPurpose(purpose).label}: {trip.title}"
    work_order = WorkOrder.objects.create(
        client=client,
        site=site,
        assigned_employee=employee or trip.lead,
        scheduled_date=planned_date,
        note=note,
    )
    last = trip.stops.aggregate(last=Max("position"))["last"] or 0
    return TripStop.objects.create(
        trip=trip,
        position=last + 1,
        site=site,
        client=client,
        work_order=work_order,
        planned_date=planned_date,
        planned_count=data.planned_count,
        contact_name=data.contact_name.strip(),
        contact_phone=data.contact_phone.strip(),
        note=data.note.strip()[:300],
        added_on_site=data.added_on_site,
        purpose=purpose,
    )


# ---------------------------------------------------------------------------
# День поверителя в городе
# ---------------------------------------------------------------------------
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def city_title(date: dt.date) -> str:
    return f"{getattr(settings, 'CITY_NAME', 'Киров')} — {date.day} {MONTHS[date.month - 1]}"


def city_day_for(employee: Employee, date: dt.date) -> Trip:
    """Выезд «город, день поверителя»: один на сотрудника и дату, заводится сам."""
    trip = (
        Trip.objects.filter(kind=TripKind.CITY, lead=employee, date_start=date)
        .exclude(status=TripStatus.CANCELLED)
        .order_by("id")
        .first()
    )
    if trip is None:
        trip = Trip.objects.create(
            kind=TripKind.CITY, title=city_title(date), destination=getattr(settings, "CITY_NAME", "Киров"),
            date_start=date, date_end=date, lead=employee,
        )
    return trip


def window_for(time: dt.time | None) -> tuple[dt.time | None, dt.time | None]:
    """Окно ожидания из времени, обещанного клиенту на сайте: с него и ``CITY_WINDOW_MINUTES`` вперёд."""
    if time is None:
        return None, None
    minutes = int(getattr(settings, "CITY_WINDOW_MINUTES", 120))
    end = (dt.datetime.combine(dt.date(2000, 1, 1), time) + dt.timedelta(minutes=minutes)).time()
    return time, (end if end > time else dt.time(23, 59))


@transaction.atomic
def attach_to_city_day(order: WorkOrder) -> TripStop | None:
    """Наряд на выезд в городе с датой → адрес в дне поверителя.

    Заявка с сайта подтверждается диспетчером, и адрес сразу встаёт в день
    исполнителя: там его увидят на карте и уложат в маршрут вместе с остальными.
    Наряды без даты, заказы (вкладка «Приёмки») и уже привязанные не трогает.
    """
    if order.kind != WorkOrderKind.FIELD or order.scheduled_date is None or order.is_closed:
        return None
    if TripStop.objects.filter(work_order=order).exists():
        return None
    trip = city_day_for(order.assigned_employee, order.scheduled_date)
    start, end = window_for(order.scheduled_time)
    request = order.request
    last = trip.stops.aggregate(last=Max("position"))["last"] or 0
    return TripStop.objects.create(
        trip=trip, position=last + 1, site=order.site, client=order.client, work_order=order,
        planned_date=order.scheduled_date, window_start=start, window_end=end,
        planned_count=sum(item.quantity for item in request.items.all()) if request else None,
        contact_name=order.client.name if order.client.kind == ClientKind.PERSON else "",
        contact_phone=order.client.phone,
        note=(request.comment[:300] if request and request.comment else order.note[:300] if order.note else ""),
    )


# ---------------------------------------------------------------------------
# Забрать коробку с предприятия (режим «Город»)
# ---------------------------------------------------------------------------
def _lab_site(client: Client) -> Site:
    """Объект приёмки — юридический адрес заказчика, как у заказов во вкладке «Приёмки»."""
    address = client.legal_address or client.name
    site = Site.objects.filter(client=client, address=address).first()
    return site or Site.objects.create(client=client, address=address)


@transaction.atomic
def add_pickup(employee: Employee, date: dt.date, client: Client, data: StopInput, *,
               window: tuple[dt.time | None, dt.time | None] = (None, None)) -> TripStop:
    """Адрес «забрать приборы» в дне поверителя: юрлицо, контакт, сколько приборов примерно."""
    trip = city_day_for(employee, date)
    data.purpose = StopPurpose.PICKUP
    data.planned_date = date
    if not data.address.strip() and not data.site_id:
        data.address = client.legal_address
    stop = add_stop(trip, data, employee=employee, client=client)
    if window[0] or window[1]:
        stop.window_start, stop.window_end = window
        stop.save(update_fields=["window_start", "window_end"])
    return stop


@transaction.atomic
def mark_picked_up(stop: TripStop, *, count: int | None = None, note: str = "") -> tuple[WorkOrder, bool]:
    """«Забрал» одним действием: адрес выполнен, заведена приёмка (наряд ``lab``) с этим заказчиком.

    Идемпотентно: повтор возвращает уже заведённую приёмку (второй элемент — заведена ли сейчас).
    """
    stop = TripStop.objects.select_for_update().select_related("trip", "site", "client", "work_order").get(pk=stop.pk)
    if stop.purpose != StopPurpose.PICKUP:
        raise TripError("Это не забор приборов — отметьте адрес обычной отметкой")
    if stop.lab_order_id:
        return stop.lab_order, False
    if count is not None:
        stop.planned_count = count
    who = stop.work_order.assigned_employee if stop.work_order_id else stop.trip.lead
    parts = [f"Привезено с выезда «{stop.trip.title}» (адрес №{stop.pk}): {stop.site.address}"]
    if stop.planned_count:
        parts.append(f"приборов ≈ {stop.planned_count}")
    if note.strip():
        parts.append(note.strip())
    lab = WorkOrder.objects.create(
        kind=WorkOrderKind.LAB, client=stop.client, site=_lab_site(stop.client), assigned_employee=who,
        status=WorkOrderStatus.IN_PROGRESS, scheduled_date=timezone.localdate(),
        contact=", ".join(filter(None, [stop.contact_name, stop.contact_phone]))[:200],
        note="; ".join(parts), procurement=stop.trip.procurement, received_at=timezone.now(),
    )
    stop.lab_order = lab
    stop.save(update_fields=["lab_order", "planned_count"])
    set_stop_status(stop, StopStatus.DONE)
    return lab, True


@transaction.atomic
def add_delivery(order: WorkOrder, employee: Employee, date: dt.date, *,
                 window: tuple[dt.time | None, dt.time | None] = (None, None), note: str = "") -> tuple[TripStop, bool]:
    """Обратный выезд «вернуть приборы» в дне поверителя. Повтор — тот же ждущий адрес."""
    if order.kind != WorkOrderKind.LAB:
        raise TripError("Вернуть можно только приборы приёмки")
    if order.issued_at:
        raise TripError("Приборы уже выданы заказчику")
    existing = order.logistic_stops.filter(purpose=StopPurpose.DELIVERY, status=StopStatus.PLANNED).first()
    if existing is not None:
        return existing, False
    pickup = order.logistic_stops.filter(purpose=StopPurpose.PICKUP).select_related("site").first()
    trip = city_day_for(employee, date)
    data = StopInput(
        site_id=pickup.site_id if pickup else None,
        address="" if pickup else (order.client.legal_address or order.site.address),
        contact_name=pickup.contact_name if pickup else order.contact[:160],
        contact_phone=pickup.contact_phone if pickup else order.client.phone,
        planned_count=order.verifications.count() or (pickup.planned_count if pickup else None),
        planned_date=date, note=note or f"Вернуть приборы приёмки №{order.pk}", purpose=StopPurpose.DELIVERY,
    )
    stop = add_stop(trip, data, employee=employee, client=order.client)
    stop.lab_order = order
    fields = ["lab_order"]
    if window[0] or window[1]:
        stop.window_start, stop.window_end = window
        fields += ["window_start", "window_end"]
    stop.save(update_fields=fields)
    return stop, True


def parse_lines(text: str) -> list[StopInput]:
    """«Адрес; ФИО; телефон; сколько приборов» — по адресу в строке, всё после адреса необязательно."""
    result: list[StopInput] = []
    for raw in (text or "").splitlines():
        parts = [part.strip() for part in raw.split(";")]
        if not parts or not parts[0]:
            continue
        item = StopInput(address=parts[0], locate=False)
        for extra in parts[1:]:
            digits = "".join(ch for ch in extra if ch.isdigit())
            if extra.isdigit() and len(extra) <= 3:
                item.planned_count = int(extra)
            elif len(digits) >= 6 and len(digits) >= len(extra.replace(" ", "")) - 4:
                item.contact_phone = extra
            elif extra and not item.client_name:
                item.client_name = extra
                item.contact_name = extra
            elif extra:
                item.note = extra
        result.append(item)
    return result


def set_stop_status(stop: TripStop, status: str) -> TripStop:
    """Отметка по адресу. Пустой наряд несостоявшегося визита не висит в «Приёмках» открытым."""
    if status not in StopStatus.values:
        raise TripError("Неизвестная отметка")
    stop.status = status
    stop.visited_at = None if status == StopStatus.PLANNED else timezone.now()
    stop.save(update_fields=["status", "visited_at"])

    order = stop.work_order
    if order is not None and not order.verifications.exists():
        wanted = WorkOrderStatus.PLANNED if status in (StopStatus.PLANNED, StopStatus.DONE) else WorkOrderStatus.CANCELLED
        if status == StopStatus.DONE and stop.purpose != StopPurpose.VISIT:
            # Забор/возврат поверок на месте не даёт: съездили — наряд выезда закрыт.
            wanted = WorkOrderStatus.DONE
        if order.status != wanted:
            order.status = wanted
            order.closed_at = timezone.now() if wanted == WorkOrderStatus.DONE else None
            order.save(update_fields=["status", "closed_at"])
    if stop.purpose == StopPurpose.DELIVERY and stop.lab_order_id:
        # Отвезли приборы обратно — приёмка выдана; вернули отметку — выдача снимается.
        lab = stop.lab_order
        if status == StopStatus.DONE and lab.issued_at is None:
            lab.issued_at = timezone.now()
            lab.save(update_fields=["issued_at"])
        elif status == StopStatus.PLANNED and lab.issued_at is not None:
            lab.issued_at = None
            lab.save(update_fields=["issued_at"])
    trip = stop.trip
    if status != StopStatus.PLANNED and trip.status == TripStatus.PLANNED:
        trip.status = TripStatus.ACTIVE
        trip.save(update_fields=["status"])
    return stop


@transaction.atomic
def remove_stop(stop: TripStop) -> None:
    order = stop.work_order
    if order is not None and order.verifications.exists():
        raise TripError("По адресу уже есть поверки — удалить нельзя, поставьте отметку")
    stop.delete()
    if order is not None:
        order.delete()


@transaction.atomic
def reorder(trip: Trip, stop_ids: list[int]) -> None:
    stops = {stop.pk: stop for stop in trip.stops.all()}
    ordered = [stops[pk] for pk in stop_ids if pk in stops]
    ordered += [stop for pk, stop in stops.items() if pk not in set(stop_ids)]
    for index, stop in enumerate(ordered, start=1):
        if stop.position != index:
            stop.position = index
            stop.save(update_fields=["position"])


def locate_missing(trip: Trip, *, limit: int = 20) -> dict:
    """Найти координаты адресам без точки. Порциями: Nominatim просит не чаще запроса в секунду."""
    import time

    pending = list(
        Site.objects.filter(trip_stops__trip=trip, latitude__isnull=True).distinct().order_by("id")
    )
    found = 0
    for index, site in enumerate(pending[:limit]):
        if index and geocode.mode() != "dadata":
            time.sleep(1.0)
        point = geocode.geocode(site.address, trip.destination)
        if point:
            site.latitude, site.longitude = _coord(point[0]), _coord(point[1])
            site.save(update_fields=["latitude", "longitude"])
            found += 1
    tried = min(len(pending), limit)
    return {"found": found, "not_found": tried - found, "remaining": len(pending) - tried}


@transaction.atomic
def next_visit(
    source: Trip, *, title: str = "", date_start: dt.date | None = None, date_end: dt.date | None = None,
) -> Trip:
    """Следующий заход: новая командировка с адресами, которые в этом не отработаны.

    У закупки это обычное дело — контракт исполняется в несколько приездов.
    Адреса переносятся со своими заметками, наряды под них заводятся новые:
    так поверки каждого захода считаются отдельно.
    """
    number = (Trip.objects.filter(procurement_id=source.procurement_id).count() + 1) if source.procurement_id else None
    trip = Trip.objects.create(
        kind=source.kind,
        title=title.strip() or _next_title(source, number),
        destination=source.destination,
        procurement=source.procurement,
        date_start=date_start,
        date_end=date_end,
        lead=source.lead,
        note=source.note,
    )
    trip.members.set(source.members.all())
    for stop in source.stops.filter(status__in=UNFINISHED).select_related("site", "client"):
        order = WorkOrder.objects.create(
            client=stop.client, site=stop.site, assigned_employee=trip.lead,
            scheduled_date=date_start, note=f"Командировка: {trip.title}",
        )
        TripStop.objects.create(
            trip=trip, position=stop.position, site=stop.site, client=stop.client, work_order=order,
            planned_date=date_start, planned_count=stop.planned_count,
            contact_name=stop.contact_name, contact_phone=stop.contact_phone, note=stop.note,
            carried_from=stop,
        )
    return trip


def _next_title(source: Trip, number: int | None) -> str:
    if number:
        base = source.title.split(" — заход")[0]
        return f"{base} — заход {number}"
    return f"{source.title} (продолжение)"


# ---------------------------------------------------------------------------
# План / факт
# ---------------------------------------------------------------------------
READY = (VerificationStatus.READY, VerificationStatus.ACCEPTED)


def stops_with_counts(trip: Trip):
    return (
        trip.stops.select_related("site", "client", "work_order", "carried_from__trip")
        .annotate(
            verifications_total=Count("work_order__verifications", distinct=True),
            verifications_ready=Count(
                "work_order__verifications", filter=Q(work_order__verifications__status__in=READY), distinct=True
            ),
            verifications_unsuitable=Count(
                "work_order__verifications",
                filter=Q(work_order__verifications__status__in=READY, work_order__verifications__suitable=False),
                distinct=True,
            ),
        )
        .order_by("position", "id")
    )


def summarize(stops) -> dict:
    stops = list(stops)
    by_status = {value: 0 for value in StopStatus.values}
    for stop in stops:
        by_status[stop.status] += 1
    return {
        "stops_total": len(stops),
        "stops_done": by_status[StopStatus.DONE],
        "stops_waiting": by_status[StopStatus.PLANNED],
        "stops_no_answer": by_status[StopStatus.NO_ANSWER],
        "stops_postponed": by_status[StopStatus.POSTPONED],
        "stops_refused": by_status[StopStatus.REFUSED],
        "stops_on_site": sum(1 for stop in stops if stop.added_on_site),
        "instruments_planned": sum(stop.planned_count or 0 for stop in stops),
        "instruments_entered": sum(stop.verifications_total for stop in stops),
        "instruments_ready": sum(stop.verifications_ready for stop in stops),
        "instruments_unsuitable": sum(stop.verifications_unsuitable for stop in stops),
    }


def trip_counts(trip_ids: list[int]) -> dict[int, dict]:
    """Короткая сводка для списка командировок — одним запросом на все."""
    rows = (
        TripStop.objects.filter(trip_id__in=trip_ids)
        .values("trip_id")
        .annotate(
            stops_total=Count("id", distinct=True),
            stops_done=Count("id", filter=Q(status=StopStatus.DONE), distinct=True),
            instruments_entered=Count("work_order__verifications", distinct=True),
        )
    )
    return {row["trip_id"]: row for row in rows}


def procurement_progress(procurement: Procurement) -> dict:
    """Объём по контракту: сколько приборов надо и сколько поверено за все заходы."""
    planned = sum(item.quantity for item in procurement.items.all())
    verifications = Verification.objects.filter(
        Q(work_order__trip_stop__trip__procurement=procurement) | Q(work_order__procurement=procurement)
    ).distinct()
    return {
        "planned": planned,
        "entered": verifications.count(),
        "ready": verifications.filter(status__in=READY).count(),
        "unsuitable": verifications.filter(status__in=READY, suitable=False).count(),
    }


def procurement_progress_many(procurements: list[Procurement]) -> dict[int, dict]:
    """То же, что ``procurement_progress``, для списка закупок — тремя запросами на весь список.

    Поверка относится к закупке через адрес захода (командировка по закупке) или через
    заказ «в счёт контракта»; если заданы оба — считается по захода.
    """
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    ids = [item.pk for item in procurements]
    result = {pk: {"planned": 0, "entered": 0, "ready": 0, "unsuitable": 0} for pk in ids}
    if not ids:
        return result
    for row in (
        ProcurementItem.objects.filter(procurement_id__in=ids).values("procurement_id").annotate(total=Sum("quantity"))
    ):
        result[row["procurement_id"]]["planned"] = row["total"] or 0
    rows = (
        Verification.objects.annotate(
            proc=Coalesce("work_order__trip_stop__trip__procurement_id", "work_order__procurement_id")
        )
        .filter(proc__in=ids)
        .values("proc")
        .annotate(
            entered=Count("id", distinct=True),
            ready=Count("id", filter=Q(status__in=READY), distinct=True),
            unsuitable=Count("id", filter=Q(status__in=READY, suitable=False), distinct=True),
        )
    )
    for row in rows:
        result[row["proc"]].update(entered=row["entered"], ready=row["ready"], unsuitable=row["unsuitable"])
    return result


def pass_coverage(trip: Trip) -> list[dict]:
    """Кто из едущих с пропуском на даты командировки, а кто без. Только для закупок."""
    if not trip.procurement_id:
        return []
    people = [trip.lead, *trip.members.exclude(pk=trip.lead_id)]
    passes = list(trip.procurement.passes.filter(employee__isnull=False))
    result = []
    for person in people:
        own = [item for item in passes if item.employee_id == person.pk]
        covering = next((item for item in own if item.covers(trip.date_start, trip.date_end)), None)
        pending = next((item for item in own if item.status in (PassStatus.NEEDED, PassStatus.REQUESTED)), None)
        if covering:
            state, text = "ok", f"пропуск до {covering.valid_to:%d.%m.%Y}" if covering.valid_to else "пропуск оформлен"
        elif pending:
            state, text = "pending", pending.get_status_display().lower()
        elif own:
            state, text = "missing", "пропуск не действует на эти даты"
        else:
            state, text = "missing", "пропуска нет"
        result.append({"employee_id": person.pk, "full_name": person.full_name, "state": state, "text": text})
    return result


def sync_kind(trip: Trip) -> None:
    trip.kind = TripKind.PROCUREMENT if trip.procurement_id else TripKind.CLASSIC
