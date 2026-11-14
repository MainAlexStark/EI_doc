from django.urls import path

from apps.logistics import api

urlpatterns = [
    path("trips/", api.TripListView.as_view(), name="trip_list"),
    path("trips/<int:pk>/", api.TripDetailView.as_view(), name="trip_detail"),
    path("trips/<int:pk>/stops/", api.TripStopsView.as_view(), name="trip_stops"),
    path("trips/<int:pk>/stops/bulk/", api.TripStopsBulkView.as_view(), name="trip_stops_bulk"),
    path("trips/<int:pk>/stops/reorder/", api.TripStopsReorderView.as_view(), name="trip_stops_reorder"),
    path("trips/<int:pk>/locate/", api.TripLocateView.as_view(), name="trip_locate"),
    path("trips/<int:pk>/next-visit/", api.TripNextVisitView.as_view(), name="trip_next_visit"),
    path("trip-stops/<int:pk>/", api.TripStopDetailView.as_view(), name="trip_stop_detail"),
    path("procurements/", api.ProcurementListView.as_view(), name="procurement_list"),
    path("procurements/<int:pk>/", api.ProcurementDetailView.as_view(), name="procurement_detail"),
    path("procurements/<int:pk>/documents/", api.ProcurementDocumentsView.as_view(), name="procurement_documents"),
    path("procurement-documents/<int:pk>/", api.ProcurementDocumentView.as_view(), name="procurement_document"),
    path("procurements/<int:pk>/passes/", api.ProcurementPassesView.as_view(), name="procurement_passes"),
    path("passes/<int:pk>/", api.PassDetailView.as_view(), name="pass_detail"),
]
