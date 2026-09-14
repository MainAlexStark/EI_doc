"""/healthz — то, что дёргает deploy.sh при развёртывании и обновлении."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse


class HealthzTestCase(TestCase):
    def test_ok_without_trailing_slash_redirect(self):
        # deploy.sh делает curl -fsS без -L: редирект 301 считался бы отказом.
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_reverse_matches_no_slash_path(self):
        self.assertEqual(reverse("healthz"), "/healthz")
