"""Права ролей: кто что может — проверяем по живым эндпоинтам, а не по таблице."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Role, User
from apps.core.permissions import user_permissions


def _user(role: str, **extra) -> User:
    return User.objects.create_user(f"{role}@ei.test", "pw", role=role, **extra)


class RulesTestCase(TestCase):
    def test_observer_reads_but_cannot_change(self):
        observer = _user(Role.OBSERVER)
        self.assertTrue(observer.has_perm("verification.view_journal"))
        self.assertFalse(observer.has_perm("core.write"))
        self.assertFalse(observer.has_perm("verification.sign_protocol"))

    def test_matrix(self):
        expectations = {
            Role.OWNER: {"verification.normocontrol", "hub.manage_requests", "arshin.submit"},
            Role.HEAD: {"verification.normocontrol", "logistics.plan_trips"},
            Role.METROLOGIST: {"verification.normocontrol", "arshin.submit"},
            Role.MANAGER: {"hub.manage_requests", "logistics.plan_trips", "core.manage_contracts"},
            Role.VERIFIER: {"verification.field_work", "verification.sign_protocol"},
        }
        forbidden = {
            Role.METROLOGIST: {"hub.manage_requests"},
            Role.MANAGER: {"verification.normocontrol", "arshin.submit"},
            Role.VERIFIER: {"verification.normocontrol", "hub.manage_requests", "logistics.plan_trips"},
        }
        for role, perms in expectations.items():
            user = _user(role)
            for perm in perms:
                self.assertTrue(user.has_perm(perm), f"{role} должен иметь {perm}")
            for perm in forbidden.get(role, ()):
                self.assertFalse(user.has_perm(perm), f"{role} не должен иметь {perm}")

    def test_superuser_can_everything(self):
        admin = User.objects.create_superuser("root@ei.test", "pw", role=Role.OBSERVER)
        self.assertIn("verification.normocontrol", user_permissions(admin))


class EndpointsTestCase(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_observer_cannot_sign_or_renumber(self):
        self.client.force_authenticate(_user(Role.OBSERVER))
        self.assertEqual(self.client.post(reverse("sign_protocols"), {"protocols": [1]}, format="json").status_code, 403)
        self.assertEqual(self.client.get(reverse("normocontrol_scopes")).status_code, 403)

    def test_verifier_cannot_dispatch_requests(self):
        self.client.force_authenticate(_user(Role.VERIFIER))
        self.assertEqual(self.client.get(reverse("request_list")).status_code, 403)

    def test_me_lists_permissions(self):
        self.client.force_authenticate(_user(Role.MANAGER))
        data = self.client.get(reverse("me")).json()
        self.assertIn("hub.manage_requests", data["permissions"])
        self.assertNotIn("verification.normocontrol", data["permissions"])
