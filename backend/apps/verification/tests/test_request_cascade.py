"""Заявка отражает статус своего наряда (WorkOrder.sync_request_status()).

Работник до этой правки мог отменить наряд, но заявка так и оставалась
«Подтверждена» — по ней нельзя было увидеть, что дальше ничего не будет.
Теперь наряд — источник истины: DONE/CANCELLED наряда переносится на заявку,
а возврат наряда обратно в работу откатывает заявку к CONFIRMED.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.hub.models import Request, RequestStatus
from apps.verification.models import Client, Site, Verification, VerificationStatus, WorkOrder, WorkOrderStatus
from apps.verification.tests import factories as f


class RequestCascadeTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        self.client_obj = Client.objects.create(name="Частное лицо")
        self.site = Site.objects.create(address="г. Киров, ул. Ленина, 1", client=self.client_obj)
        self.request_obj = Request.objects.create(
            contact_name="Иванов И. И.", address=self.site.address, status=RequestStatus.CONFIRMED,
        )
        self.work_order = WorkOrder.objects.create(
            request=self.request_obj, client=self.client_obj, site=self.site, assigned_employee=self.employee,
        )
        user = User.objects.create_user(email="d@ei.test", password="pass12345", role=Role.MANAGER)
        self.api = APIClient()
        self.api.force_authenticate(user)

    def make_verification(self, serial: str) -> Verification:
        verification = f.make_verification(self.family, self.employee, day=1, serial=serial)
        verification.work_order = self.work_order
        verification.save()
        return verification

    def set_status(self, status: str):
        return self.api.post(
            reverse("work_order_status", args=[self.work_order.pk]), {"status": status}, format="json"
        )

    # --- автопуть: наряд закрывается/переоткрывается по поверкам ---

    def test_work_order_done_marks_request_done(self):
        v1 = self.make_verification("SN001")
        v1.status = VerificationStatus.ACCEPTED
        v1.save()
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.DONE
        self.request_obj.refresh_from_db()
        assert self.request_obj.status == RequestStatus.DONE

    def test_reopening_done_work_order_reverts_request_to_confirmed(self):
        v1 = self.make_verification("SN001")
        v1.status = VerificationStatus.ACCEPTED
        v1.save()
        self.request_obj.refresh_from_db()
        assert self.request_obj.status == RequestStatus.DONE

        v1.status = VerificationStatus.REJECTED
        v1.save()
        self.work_order.refresh_from_db()
        assert self.work_order.status == WorkOrderStatus.IN_PROGRESS
        self.request_obj.refresh_from_db()
        assert self.request_obj.status == RequestStatus.CONFIRMED

    # --- ручной путь: отмена наряда и возврат из отмены (WorkOrderStatusView) ---

    def test_cancelling_work_order_marks_request_cancelled(self):
        response = self.set_status(WorkOrderStatus.CANCELLED)
        assert response.status_code == 200
        self.request_obj.refresh_from_db()
        assert self.request_obj.status == RequestStatus.CANCELLED

    def test_reverting_cancellation_marks_request_confirmed_again(self):
        self.set_status(WorkOrderStatus.CANCELLED)
        response = self.set_status(WorkOrderStatus.PLANNED)
        assert response.status_code == 200
        self.request_obj.refresh_from_db()
        assert self.request_obj.status == RequestStatus.CONFIRMED

    # --- защита от неожиданностей ---

    def test_work_order_without_request_is_not_affected(self):
        standalone = WorkOrder.objects.create(client=self.client_obj, site=self.site, assigned_employee=self.employee)
        response = self.api.post(
            reverse("work_order_status", args=[standalone.pk]), {"status": WorkOrderStatus.CANCELLED}, format="json"
        )
        assert response.status_code == 200  # просто не падает — request_id is None

    def test_rejected_request_is_not_overwritten_by_unrelated_work_order_change(self):
        self.request_obj.status = RequestStatus.REJECTED
        self.request_obj.save(update_fields=["status"])
        self.set_status(WorkOrderStatus.CANCELLED)
        self.request_obj.refresh_from_db()
        assert self.request_obj.status == RequestStatus.REJECTED
