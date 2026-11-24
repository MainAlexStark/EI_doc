"""SEO сайта: свой head и текст под каждый адрес, robots.txt, sitemap.xml."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

INDEX = """<html><head>
    <!--seo-->
    <title>по умолчанию</title>
    <!--/seo-->
</head><body><div id="root"></div></body></html>"""


@override_settings(APP_PUBLIC_URL="https://ei.example")
class SeoPagesTestCase(TestCase):
    def get(self, path):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "index.html").write_text(INDEX, encoding="utf-8")
            with override_settings(FRONTEND_DIST_DIR=Path(tmp)):
                response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def json_ld(self, html):
        raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1)
        return json.loads(raw)["@graph"]

    def test_meters_kirov(self):
        html = self.get("/schetchiki/")
        self.assertIn("<title>Поверка счётчиков воды в Кирове", html)
        self.assertNotIn("по умолчанию", html)
        self.assertIn('<link rel="canonical" href="https://ei.example/schetchiki/" />', html)
        self.assertIn("<h1>Поверка счётчиков Киров</h1>", html)
        graph = self.json_ld(html)
        offers = next(item for item in graph if item["@type"] == "Service")["offers"]
        self.assertEqual([o["price"] for o in offers], ["500", "600"])

    def test_meters_yaransk_has_own_page(self):
        html = self.get("/schetchiki/yaransk/")
        self.assertIn("в Яранске", html)
        self.assertIn("<h1>Поверка счётчиков Яранск</h1>", html)
        self.assertIn('href="https://ei.example/schetchiki/yaransk/"', html)

    def test_business(self):
        html = self.get("/biznes/")
        self.assertIn("<h1>Метрология для вашего бизнеса</h1>", html)
        service = next(item for item in self.json_ld(html) if item["@type"] == "Service")
        self.assertEqual(service["areaServed"]["name"], "Россия")

    def test_old_address_points_to_new(self):
        html = self.get("/o-nas/")
        self.assertIn('<link rel="canonical" href="https://ei.example/schetchiki/" />', html)

    def test_cabinet_not_indexed(self):
        html = self.get("/")
        self.assertIn('content="noindex, follow"', html)
        self.assertIn('<div id="root"></div>', html)

    def test_robots_and_sitemap(self):
        robots = self.client.get("/robots.txt").content.decode()
        self.assertIn("Sitemap: https://ei.example/sitemap.xml", robots)
        sitemap = self.client.get("/sitemap.xml").content.decode()
        for path in ("/schetchiki/", "/schetchiki/yaransk/", "/biznes/"):
            self.assertIn(f"<loc>https://ei.example{path}</loc>", sitemap)
