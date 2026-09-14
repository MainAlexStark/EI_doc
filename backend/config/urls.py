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

admin.site.site_header = "EI_doc"
admin.site.site_title = "EI_doc"
admin.site.index_title = "Документооборот поверочных работ"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/catalog/si-types/suggest/", SiTypeSuggestView.as_view(), name="si_type_suggest"),
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
