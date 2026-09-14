"""Корень "/" — отдаёт index.html собранного фронтенда журнала."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings


class FrontendIndexTestCase(TestCase):
    def test_returns_501_when_not_built(self):
        # В тестовом окружении frontend_dist не собирается — как и в
        # локальной разработке без Docker (там фронт отдельным процессом,
        # см. README).
        response = self.client.get("/")
        self.assertEqual(response.status_code, 501)

    def test_serves_built_index_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("<html>журнал</html>", encoding="utf-8")
            with override_settings(FRONTEND_DIST_DIR=dist):
                response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/html")
        self.assertIn("журнал", response.content.decode("utf-8"))
