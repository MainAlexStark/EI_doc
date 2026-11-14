"""Открытые наряды с датой — в дни поверителей (вкладка «Выезды»).

До этого выезды по городу жили отдельными нарядами во вкладке «Поверки».
Теперь «Поверки» — заказы от заказчиков, а адреса — во «Выездах». Логика та
же, что у apps.logistics.service.attach_to_city_day, но на исторических моделях.
"""

import datetime as dt

from django.db import migrations

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def forwards(apps, schema_editor):
    WorkOrder = apps.get_model("verification", "WorkOrder")
    Trip = apps.get_model("logistics", "Trip")
    TripStop = apps.get_model("logistics", "TripStop")
    orders = (
        WorkOrder.objects.filter(kind="field", status__in=["planned", "in_progress"], scheduled_date__isnull=False)
        .filter(trip_stop__isnull=True)
        .select_related("client")
        .order_by("scheduled_date", "scheduled_time", "id")
    )
    for order in orders:
        trip = (
            Trip.objects.filter(kind="city", lead_id=order.assigned_employee_id, date_start=order.scheduled_date)
            .exclude(status="cancelled")
            .order_by("id")
            .first()
        )
        if trip is None:
            date = order.scheduled_date
            trip = Trip.objects.create(
                kind="city", title=f"Киров — {date.day} {MONTHS[date.month - 1]}", destination="Киров",
                date_start=date, date_end=date, lead_id=order.assigned_employee_id, route={},
            )
        start = order.scheduled_time
        end = None
        if start is not None:
            end = (dt.datetime.combine(dt.date(2000, 1, 1), start) + dt.timedelta(hours=2)).time()
            if end <= start:
                end = dt.time(23, 59)
        position = TripStop.objects.filter(trip=trip).count() + 1
        TripStop.objects.create(
            trip=trip, position=position, site_id=order.site_id, client_id=order.client_id, work_order=order,
            planned_date=order.scheduled_date, window_start=start, window_end=end,
            contact_phone=order.client.phone or "", status="planned",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("logistics", "0002_alter_historicaltrip_options_alter_trip_options_and_more"),
        ("verification", "0009_client_kpp_client_legal_address_client_ogrn_and_more"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
