from django.urls import path

from apps.equipment import api

urlpatterns = [
    path("programs/", api.ProgramListView.as_view(), name="attestation_programs"),
    path("", api.AttestationListView.as_view(), name="attestation_list"),
    path("<int:pk>/", api.AttestationDetailView.as_view(), name="attestation_detail"),
    path("<int:pk>/measurements/", api.MeasurementsView.as_view(), name="attestation_measurements"),
    path("<int:pk>/approve-plan/", api.ApprovePlanView.as_view(), name="attestation_approve_plan"),
    path("<int:pk>/sign/", api.SignView.as_view(), name="attestation_sign"),
    path("<int:pk>/protocol.<str:fmt>", api.ProtocolFileView.as_view(), name="attestation_protocol"),
]
