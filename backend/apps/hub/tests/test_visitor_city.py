"""Город посетителя для заголовка сайта «Поверка счётчиков Киров/Яранск»."""

from __future__ import annotations

from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.hub.address import DaDataClient


class VisitorCityViewTestCase(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()
        cache.clear()

    @mock.patch("apps.hub.api.DaDataClient")
    def test_known_city_maps_to_site_key(self, client_cls):
        client_cls.return_value.iplocate_city.return_value = "Яранск"
        response = self.api.get(reverse("visitor_city"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"city": "yaransk"})

    @mock.patch("apps.hub.api.DaDataClient")
    def test_other_city_is_empty(self, client_cls):
        client_cls.return_value.iplocate_city.return_value = "Москва"
        self.assertEqual(self.api.get(reverse("visitor_city")).json(), {"city": ""})


class IplocateTestCase(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_without_key_returns_empty(self):
        self.assertEqual(DaDataClient(api_key="").iplocate_city("1.2.3.4"), "")

    @mock.patch("apps.hub.address.requests.get")
    def test_parses_city(self, get):
        get.return_value.json.return_value = {"location": {"data": {"city": "Киров"}}}
        self.assertEqual(DaDataClient(api_key="k").iplocate_city("1.2.3.4"), "Киров")

    @mock.patch("apps.hub.address.requests.get", side_effect=requests.ConnectionError("down"))
    def test_network_error_is_empty(self, _get):
        self.assertEqual(DaDataClient(api_key="k").iplocate_city("1.2.3.4"), "")


class SitePathsTestCase(TestCase):
    def test_site_paths_resolve(self):
        for name, path in (("public_site_meters", "/schetchiki/"), ("public_site_business", "/biznes/")):
            self.assertEqual(reverse(name), path)
