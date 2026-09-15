"""Раздача sw.js/manifest.webmanifest из корня — scope service worker'а."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings


class FrontendRootFileTestCase(TestCase):
    def test_serves_known_pwa_files_from_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "sw.js").write_text("self.addEventListener('fetch', () => {});", encoding="utf-8")
            (dist / "manifest.webmanifest").write_text('{"name":"EI_doc"}', encoding="utf-8")
            (dist / "workbox-abc123.js").write_text("/* workbox */", encoding="utf-8")
            (dist / "index.html").write_text("<html>кабинет</html>", encoding="utf-8")
            with override_settings(FRONTEND_DIST_DIR=dist):
                sw = self.client.get("/sw.js")
                manifest = self.client.get("/manifest.webmanifest")
                workbox = self.client.get("/workbox-abc123.js")
                index = self.client.get("/index.html")

        self.assertEqual(sw.status_code, 200)
        self.assertEqual(sw["Content-Type"], "text/javascript")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest["Content-Type"], "application/manifest+json")
        self.assertEqual(workbox.status_code, 200)
        # precache-манифест sw.js ссылается на "index.html" относительным
        # путём (см. vite.config.ts) — он должен резолвиться в /index.html
        # с тем же содержимым, что и "/" (frontend_index).
        self.assertEqual(index.status_code, 200)
        self.assertEqual(index["Content-Type"], "text/html")
        self.assertIn("кабинет", index.content.decode("utf-8"))

    def test_404_when_not_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(FRONTEND_DIST_DIR=Path(tmp)):
                response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 404)

    def test_rejects_filenames_outside_the_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "secret.env").write_text("SECRET=1", encoding="utf-8")
            with override_settings(FRONTEND_DIST_DIR=dist):
                response = self.client.get("/secret.env")
        self.assertEqual(response.status_code, 404)
