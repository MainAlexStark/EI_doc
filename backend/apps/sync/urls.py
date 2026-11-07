from django.urls import path

from apps.sync import api

urlpatterns = [
    path("pull/", api.PullView.as_view(), name="sync_pull"),
    path("devices/", api.DevicesView.as_view(), name="sync_devices"),
]
