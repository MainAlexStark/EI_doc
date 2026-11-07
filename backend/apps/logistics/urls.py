from django.urls import path

from apps.logistics import api

urlpatterns = [
    path("map-config/", api.MapConfigView.as_view(), name="logistics_map_config"),
    path("demand/", api.DemandView.as_view(), name="logistics_demand"),
    path("trips/", api.TripListView.as_view(), name="logistics_trips"),
    path("trips/<int:pk>/", api.TripDetailView.as_view(), name="logistics_trip"),
    path("trips/<int:pk>/optimize/", api.TripOptimizeView.as_view(), name="logistics_trip_optimize"),
    path("trips/<int:pk>/standards/", api.TripReserveView.as_view(), name="logistics_trip_standards"),
    path("trips/<int:pk>/pack/", api.TripPackView.as_view(), name="logistics_trip_pack"),
    path("stops/<int:pk>/", api.TripStopView.as_view(), name="logistics_stop"),
    path("standards/", api.StandardOptionsView.as_view(), name="logistics_standards"),
]
