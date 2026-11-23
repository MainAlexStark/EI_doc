from django.urls import path

from apps.equipment import api

urlpatterns = [
    path("programs/", api.ProgramListView.as_view(), name="attestation_programs"),
    path("programs/<int:pk>/", api.ProgramDetailView.as_view(), name="attestation_program"),
    path("programs/<int:pk>/approve/", api.ProgramApproveView.as_view(), name="attestation_program_approve"),
    path("programs/<int:pk>/check/", api.ProgramCheckView.as_view(), name="attestation_program_check"),
    path("programs/<int:pk>/apply/", api.ProgramApplyView.as_view(), name="attestation_program_apply"),
    path("<int:pk>/save-as-program/", api.SaveAsProgramView.as_view(), name="attestation_save_as_program"),
    path("", api.AttestationListView.as_view(), name="attestation_list"),
    path("<int:pk>/", api.AttestationDetailView.as_view(), name="attestation_detail"),
    path("<int:pk>/measurements/", api.MeasurementsView.as_view(), name="attestation_measurements"),
    path("<int:pk>/approve-plan/", api.ApprovePlanView.as_view(), name="attestation_approve_plan"),
    path("<int:pk>/sign/", api.SignView.as_view(), name="attestation_sign"),
    path("<int:pk>/protocol.<str:fmt>", api.ProtocolFileView.as_view(), name="attestation_protocol"),
]
