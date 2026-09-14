from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.catalog.api import SiTypeSuggestView
from apps.verification.api import (
    LayoutsView,
    VerificationConfirmRowsView,
    VerificationMeasurementsView,
)
from apps.verification.api_journal import (
    JournalExportView,
    JournalView,
    NormocontrolScopesView,
    NumberingApplyView,
    NumberingPreviewView,
    SignProtocolsView,
)

admin.site.site_header = "EI_doc"
admin.site.site_title = "EI_doc"
admin.site.index_title = "Документооборот поверочных работ"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/catalog/si-types/suggest/", SiTypeSuggestView.as_view(), name="si_type_suggest"),
    path("api/journal/", JournalView.as_view(), name="journal"),
    path("api/journal/export/", JournalExportView.as_view(), name="journal_export"),
    path("api/normocontrol/scopes/", NormocontrolScopesView.as_view(), name="normocontrol_scopes"),
    path(
        "api/normocontrol/scopes/<int:pk>/preview/",
        NumberingPreviewView.as_view(), name="numbering_preview",
    ),
    path(
        "api/normocontrol/scopes/<int:pk>/assign/",
        NumberingApplyView.as_view(), name="numbering_apply",
    ),
    path("api/normocontrol/sign/", SignProtocolsView.as_view(), name="sign_protocols"),
    path("api/verifications/layouts/", LayoutsView.as_view(), name="measurement_layouts"),
    path(
        "api/verifications/<int:pk>/measurements/",
        VerificationMeasurementsView.as_view(), name="verification_measurements",
    ),
    path(
        "api/verifications/<int:pk>/measurements/confirm/",
        VerificationConfirmRowsView.as_view(), name="verification_confirm_rows",
    ),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
