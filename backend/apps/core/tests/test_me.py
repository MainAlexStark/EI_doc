"""GET /api/core/employees/me/ — имя и роль текущего пользователя для шапки SPA."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User


class MeViewTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()

    def test_returns_employee_full_name_and_role(self):
        user = User.objects.create_user(email="v@ei.test", password="pass12345", role=Role.VERIFIER)
        Employee.objects.create(
            tab_number="50", full_name="Кабинетов К. К.", position="Поверитель",
            user=user, telegram_chat_id="12345",
        )
        self.api.force_authenticate(user)

        response = self.api.get(reverse("me"))

        assert response.status_code == 200
        assert response.data["email"] == "v@ei.test"
        assert response.data["role"] == Role.VERIFIER
        assert response.data["role_display"] == "Поверитель"
        assert response.data["employee"]["full_name"] == "Кабинетов К. К."
        assert response.data["employee"]["telegram_linked"] is True

    def test_employee_is_null_without_a_linked_record(self):
        user = User.objects.create_user(email="obs@ei.test", password="pass12345", role=Role.OBSERVER)
        self.api.force_authenticate(user)

        response = self.api.get(reverse("me"))

        assert response.status_code == 200
        assert response.data["employee"] is None

    def test_requires_authentication(self):
        response = self.api.get(reverse("me"))
        assert response.status_code == 401
