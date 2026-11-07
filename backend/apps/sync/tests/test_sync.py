"""Полевые устройства: вход с device_id, отзыв, дельта по курсору."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Device, Role, User
from apps.verification.models import Client, Site, WorkOrder
from apps.verification.tests import factories as f

DEVICE = "phone-0001-abcdef"


class DeviceTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        self.user = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.employee.user = self.user
        self.employee.save()
        client = Client.objects.create(name="ТСЖ")
        site = Site.objects.create(client=client, address="Киров, Мира 1")
        self.other = f.make_employee(tab="07", name="Другой")
        self.mine = WorkOrder.objects.create(client=client, site=site, assigned_employee=self.employee)
        self.theirs = WorkOrder.objects.create(client=client, site=site, assigned_employee=self.other)
        self.api = APIClient()

    def login(self):
        response = self.api.post(reverse("device_token"), {
            "email": "v@ei.test", "password": "pw", "device_id": DEVICE, "label": "Redmi",
        }, format="json")
        assert response.status_code == 200, response.content
        return response.json()

    def test_device_login_registers_the_device(self):
        tokens = self.login()
        device = Device.objects.get(device_id=DEVICE)
        assert device.employee == self.employee and device.label == "Redmi"
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        assert self.api.get(reverse("me")).status_code == 200

    def test_revoked_device_loses_access_and_refresh(self):
        tokens = self.login()
        Device.objects.filter(device_id=DEVICE).update(is_revoked=True)
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        assert self.api.get(reverse("me")).status_code == 401
        self.api.credentials()
        refreshed = self.api.post(reverse("token_refresh"), {"refresh": tokens["refresh"]}, format="json")
        assert refreshed.status_code == 401
        again = self.api.post(reverse("device_token"), {"email": "v@ei.test", "password": "pw",
                                                        "device_id": DEVICE}, format="json")
        assert again.status_code == 401

    def test_refresh_keeps_the_device_binding(self):
        tokens = self.login()
        refreshed = self.api.post(reverse("token_refresh"), {"refresh": tokens["refresh"]}, format="json").json()
        Device.objects.filter(device_id=DEVICE).update(is_revoked=True)
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {refreshed['access']}")
        assert self.api.get(reverse("me")).status_code == 401

    def test_device_of_another_employee_is_refused(self):
        Device.objects.create(employee=self.other, device_id=DEVICE)
        response = self.api.post(reverse("device_token"), {"email": "v@ei.test", "password": "pw",
                                                           "device_id": DEVICE}, format="json")
        assert response.status_code == 401

    def test_pull_returns_own_orders_then_only_changes(self):
        tokens = self.login()
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        first = self.api.get(reverse("sync_pull")).json()
        assert [o["id"] for o in first["work_orders"]] == [self.mine.pk]
        assert Device.objects.get(device_id=DEVICE).last_sync_cursor == first["cursor"]

        v = f.make_verification(self.family, self.employee, day=3)
        v.work_order = self.mine
        v.save()
        self.theirs.note = "чужое"
        self.theirs.save()
        second = self.api.get(reverse("sync_pull"), {"cursor": first["cursor"]}).json()
        assert [x["client_id"] for x in second["verifications"]] == [str(v.client_id)]
        assert self.theirs.pk not in [o["id"] for o in second["work_orders"]]

    def test_observer_cannot_pull(self):
        observer = User.objects.create_user("o@ei.test", "pw")
        self.api.force_authenticate(observer)
        assert self.api.get(reverse("sync_pull")).status_code == 403
