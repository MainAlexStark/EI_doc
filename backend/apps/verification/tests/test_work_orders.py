"""Наряд закрывается сам, когда все его поверки приняты нормоконтролем."""

from __future__ import annotations

from django.test import TestCase

from apps.verification.models import Client, Site, Verification, VerificationStatus, WorkOrder, WorkOrderStatus
from apps.verification.tests import factories as f


class WorkOrderAutoCloseTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        self.client_obj = Client.objects.create(name="Частное лицо")
        self.site = Site.objects.create(address="г. Киров, ул. Ленина, 1", client=self.client_obj)
        self.work_order = WorkOrder.objects.create(
            client=self.client_obj, site=self.site, assigned_employee=self.employee,
        )

    def make_verification(self, serial: str) -> Verification:
        verification = f.make_verification(self.family, self.employee, day=1, serial=serial)
        verification.work_order = self.work_order
        verification.save()
        return verification

    def test_stays_planned_without_verifications(self):
        assert self.work_order.status == WorkOrderStatus.PLANNED

    def test_moves_to_in_progress_once_a_verification_is_attached(self):
        self.make_verification("SN001")
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.IN_PROGRESS

    def test_closes_only_when_every_verification_is_accepted(self):
        v1 = self.make_verification("SN001")
        v2 = self.make_verification("SN002")

        v1.status = VerificationStatus.ACCEPTED
        v1.save()
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.IN_PROGRESS  # v2 ещё не принята

        v2.status = VerificationStatus.ACCEPTED
        v2.save()
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.DONE
        assert self.work_order.closed_at is not None

    def test_reopens_if_a_signed_off_verification_is_sent_back(self):
        v1 = self.make_verification("SN001")
        v1.status = VerificationStatus.ACCEPTED
        v1.save()
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.DONE

        v1.status = VerificationStatus.REJECTED
        v1.save()
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.IN_PROGRESS
        assert self.work_order.closed_at is None

    def test_cancelled_work_order_is_left_alone(self):
        self.work_order.status = WorkOrderStatus.CANCELLED
        self.work_order.save()
        self.make_verification("SN001")
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.CANCELLED
