"""Уведомления: кому, когда, сводкой, с учётом настроек и «сам себе назначил»."""

from __future__ import annotations

import datetime as dt

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import MeasurementFamily, SiType
from apps.core.models import Employee, Role, User
from apps.hub.models import Request, RequestSource, RequestStatus
from apps.logistics.models import Trip, TripKind
from apps.notify import tasks
from apps.notify.actor import set_actor
from apps.notify.models import NotifyPrefs
from apps.tasks.models import Task, TaskStatus
from apps.verification.models import (
    Client, ClientKind, Instrument, Site, Verification, VerificationStatus, WorkOrder, WorkOrderKind,
)


class EventsBase(TestCase):
    def setUp(self) -> None:
        cache.clear()
        mail.outbox = []
        self.manager = User.objects.create_user(email="m@ei.test", password="x", role=Role.MANAGER)
        self.observer = User.objects.create_user(email="o@ei.test", password="x", role=Role.OBSERVER)
        self.metrologist = User.objects.create_user(email="metr@ei.test", password="x", role=Role.METROLOGIST)
        self.worker_user = User.objects.create_user(email="w@ei.test", password="x", role=Role.VERIFIER)
        self.worker = Employee.objects.create(tab_number="05", full_name="Кузнецов Иван", user=self.worker_user)
        self.other = Employee.objects.create(tab_number="06", full_name="Петров Пётр",
                                             user=User.objects.create_user(email="p@ei.test", password="x"))
        self.client_obj = Client.objects.create(kind=ClientKind.ORG, name="МУП «Водоканал»")
        self.site = Site.objects.create(client=self.client_obj, address="г Киров, ул Мира, 1")

    def tearDown(self) -> None:
        set_actor(None)

    def to(self, email):
        return [m for m in mail.outbox if m.to == [email]]

    def order(self, employee, **kw):
        return WorkOrder.objects.create(client=self.client_obj, site=self.site, assigned_employee=employee,
                                        scheduled_date=dt.date(2026, 12, 1), **kw)


class RequestEventsTests(EventsBase):
    def test_site_request_goes_to_managers_and_observers(self):
        with self.captureOnCommitCallbacks(execute=True):
            Request.objects.create(contact_name="Иванов", contact_phone="+79990000000", address="ул Ленина, 1",
                                   consent_given=True, source=RequestSource.SITE)
        assert len(self.to("m@ei.test")) == 1 and len(self.to("o@ei.test")) == 1
        assert "Иванов" in self.to("m@ei.test")[0].body
        assert self.to("w@ei.test") == [] and self.to("metr@ei.test") == []

    def test_spam_and_phone_requests_are_silent(self):
        with self.captureOnCommitCallbacks(execute=True):
            Request.objects.create(contact_name="Бот", address="x", consent_given=True, honeypot_tripped=True,
                                   status=RequestStatus.SPAM)
            Request.objects.create(contact_name="Звонок", address="x", consent_given=True, source=RequestSource.PHONE)
        assert mail.outbox == []

    def test_disabled_category_stops_email(self):
        NotifyPrefs.objects.create(user=self.manager, requests=False)
        with self.captureOnCommitCallbacks(execute=True):
            Request.objects.create(contact_name="Иванов", address="x", consent_given=True)
        assert self.to("m@ei.test") == [] and len(self.to("o@ei.test")) == 1


class AssignmentEventsTests(EventsBase):
    def test_work_orders_assigned_in_one_transaction_make_one_letter(self):
        with self.captureOnCommitCallbacks(execute=True):
            for _ in range(3):
                self.order(self.worker)
        letters = self.to("w@ei.test")
        assert len(letters) == 1 and "Вам назначено: 3" in letters[0].subject

    def test_reassignment_notifies_new_assignee_only(self):
        with self.captureOnCommitCallbacks(execute=True):
            wo = self.order(self.worker)
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            wo.note = "позвонить заранее"
            wo.save()  # исполнитель не менялся — тишина
        assert mail.outbox == []
        with self.captureOnCommitCallbacks(execute=True):
            wo.assigned_employee = self.other
            wo.save(update_fields=["assigned_employee"])
        assert self.to("w@ei.test") == [] and len(self.to("p@ei.test")) == 1

    def test_reassigned_before_flush_drops_line(self):
        with self.captureOnCommitCallbacks(execute=True):
            wo = self.order(self.worker)
            wo.assigned_employee = self.other
            wo.save()
        assert self.to("w@ei.test") == [] and len(self.to("p@ei.test")) == 1

    def test_lab_order_is_intake(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.order(self.worker, kind=WorkOrderKind.LAB)
        assert "Приёмка «МУП «Водоканал»»" in self.to("w@ei.test")[0].body

    def test_self_assignment_is_silent(self):
        set_actor(self.worker_user.pk)
        with self.captureOnCommitCallbacks(execute=True):
            self.order(self.worker, kind=WorkOrderKind.LAB)
        assert mail.outbox == []

    def test_trip_lead_and_members_but_not_city_days(self):
        with self.captureOnCommitCallbacks(execute=True):
            trip = Trip.objects.create(kind=TripKind.CLASSIC, title="Яранск", lead=self.worker,
                                       date_start=dt.date(2026, 12, 2), date_end=dt.date(2026, 12, 4))
            trip.members.add(self.other)
            Trip.objects.create(kind=TripKind.CITY, title="Город", lead=self.other, date_start=dt.date(2026, 12, 2))
        assert "Командировка «Яранск»" in self.to("w@ei.test")[0].body
        assert "старший" in self.to("w@ei.test")[0].body
        other = self.to("p@ei.test")
        assert len(other) == 1 and "участник" in other[0].body

    def test_task_assignment(self):
        with self.captureOnCommitCallbacks(execute=True):
            Task.objects.create(title="Поверить манометры", assignee=self.worker, due_date=dt.date(2026, 12, 5))
        body = self.to("w@ei.test")[0].body
        assert "Задача: Поверить манометры" in body and "05.12.2026" in body

    def test_assignments_can_be_turned_off(self):
        NotifyPrefs.objects.create(user=self.worker_user, assignments=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.order(self.worker)
        assert mail.outbox == []


class ReviewAndRemindersTests(EventsBase):
    def make_verification(self, status=VerificationStatus.READY):
        family = MeasurementFamily.objects.create(code="water", name="Счётчики воды")
        si = SiType.objects.create(registry_number="1-20", name="СВ-15", family=family)
        instrument = Instrument.objects.create(si_type=si, serial_number="123")
        return Verification.objects.create(instrument=instrument, verifier=self.worker, status=status,
                                           verified_at=timezone.now())

    def test_rejected_verification_goes_to_verifier_with_note(self):
        v = self.make_verification()
        with self.captureOnCommitCallbacks(execute=True):
            v.status = VerificationStatus.REJECTED
            v.review_note = "Нет фото пломбы"
            v.save()
        body = self.to("w@ei.test")[0].body
        assert "возвращена на доработку" in body and "Нет фото пломбы" in body
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            v.save()  # повторное сохранение — не новое событие
        assert mail.outbox == []

    def test_review_queue_digest_for_metrologists(self):
        self.make_verification()
        with self.captureOnCommitCallbacks(execute=True):
            tasks.remind_review_queue()
        assert "Ждут нормоконтроля: 1" in self.to("metr@ei.test")[0].body

    def test_tasks_due_today_and_overdue(self):
        today = timezone.localdate()
        Task.objects.create(title="Сегодня", assignee=self.worker, due_date=today)
        Task.objects.create(title="Вчера", assignee=self.worker, due_date=today - dt.timedelta(days=1))
        Task.objects.create(title="Готово", assignee=self.worker, due_date=today, status=TaskStatus.DONE)
        Task.objects.create(title="Завтра", assignee=self.worker, due_date=today + dt.timedelta(days=1))
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            tasks.remind_tasks_due()
        reminders = [m for m in self.to("w@ei.test") if "Сроки задач" in m.subject]
        body = "\n".join(m.body for m in reminders)
        assert "Сегодня" in body and "Вчера" in body and "просрочена" in body
        assert "Готово" not in body and "Завтра" not in body


class PrefsApiTests(EventsBase):
    def test_get_and_update(self):
        api = APIClient()
        api.force_authenticate(self.worker_user)
        data = api.get("/api/notify/prefs/").data
        keys = {c["key"] for c in data["categories"]}
        assert "requests" not in keys and {"assignments", "review", "reminders"} <= keys
        assert all(c["enabled"] for c in data["categories"]) and data["mail_configured"] is False
        data = api.put("/api/notify/prefs/", {"categories": {"reminders": False}}, format="json").data
        assert {c["key"]: c["enabled"] for c in data["categories"]}["reminders"] is False
        assert NotifyPrefs.wants_email(self.worker_user, "reminders") is False
        assert NotifyPrefs.wants_email(self.worker_user, "security") is True

    def test_manager_sees_requests(self):
        api = APIClient()
        api.force_authenticate(self.manager)
        assert "requests" in {c["key"] for c in api.get("/api/notify/prefs/").data["categories"]}

    def test_test_mail_only_for_admins(self):
        api = APIClient()
        api.force_authenticate(self.worker_user)
        assert api.post("/api/notify/test/").status_code == 403
        head = User.objects.create_user(email="h@ei.test", password="x", role=Role.HEAD)
        api.force_authenticate(head)
        with self.captureOnCommitCallbacks(execute=True):
            assert api.post("/api/notify/test/").status_code == 200
        assert len(self.to("h@ei.test")) == 1
