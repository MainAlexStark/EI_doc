from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.catalog.api import FamilyOptionsView, SiTypeSuggestView
from apps.core.api import EmployeeListView, TelegramLinkCodeView
from apps.core.telegram_views import TelegramWebhookView
from apps.core.views import frontend_index, healthz
from apps.hub.api import (
    AddressSuggestView,
    RequestConfirmView,
    RequestCreateView,
    RequestListView,
    RequestRejectView,
    RequestRouteView,
)
from apps.hub.api_availability import (
    AvailabilityDetailView,
    AvailabilityListCreateView,
    AvailabilityPublicSlotsView,
)
from apps.tasks.api import (
    TaskBoardView,
    TaskCalendarView,
    TaskDetailView,
    TaskListCreateView,
    TaskWorkloadView,
)
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
from apps.verification.api_workorders import (
    WorkOrderCreateView,
    WorkOrderListView,
    WorkOrderStatusView,
)

admin.site.site_header = "EI_doc"
admin.site.site_title = "EI_doc"
admin.site.index_title = "Документооборот поверочных работ"

urlpatterns = [
    # Без слэша и до admin/: deploy.sh дёргает ровно "/healthz" при
    # обновлении, а APPEND_SLASH сделал бы редирект на "/healthz/",
    # который curl -fsS без -L считает не 200 и откатывает деплой.
    path("healthz", healthz, name="healthz"),
    path("", frontend_index, name="frontend_index"),
    path("admin/", admin.site.urls),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/catalog/si-types/suggest/", SiTypeSuggestView.as_view(), name="si_type_suggest"),
    path("api/catalog/families/", FamilyOptionsView.as_view(), name="family_options"),
    path("api/core/employees/", EmployeeListView.as_view(), name="employee_list"),
    path(
        "api/core/employees/me/telegram-link-code/",
        TelegramLinkCodeView.as_view(), name="telegram_link_code",
    ),
    path(
        "api/telegram/webhook/<str:secret>/",
        TelegramWebhookView.as_view(), name="telegram_webhook",
    ),
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
    # Заявки (EI_Hub)
    path("api/hub/address-suggest/", AddressSuggestView.as_view(), name="address_suggest"),
    path("api/hub/requests/", RequestCreateView.as_view(), name="request_create"),
    path("api/hub/requests/list/", RequestListView.as_view(), name="request_list"),
    path("api/hub/requests/<int:pk>/route/", RequestRouteView.as_view(), name="request_route"),
    path("api/hub/requests/<int:pk>/confirm/", RequestConfirmView.as_view(), name="request_confirm"),
    path("api/hub/requests/<int:pk>/reject/", RequestRejectView.as_view(), name="request_reject"),
    # Доступность сотрудников — их собственный календарь + публичные слоты для формы
    path("api/hub/availability/", AvailabilityListCreateView.as_view(), name="availability_list"),
    path("api/hub/availability/<int:pk>/", AvailabilityDetailView.as_view(), name="availability_detail"),
    path(
        "api/hub/availability/slots/",
        AvailabilityPublicSlotsView.as_view(), name="availability_public_slots",
    ),
    # Наряды
    path("api/work-orders/", WorkOrderListView.as_view(), name="work_order_list"),
    path("api/work-orders/create/", WorkOrderCreateView.as_view(), name="work_order_create"),
    path("api/work-orders/<int:pk>/status/", WorkOrderStatusView.as_view(), name="work_order_status"),
    # Задачи
    path("api/tasks/", TaskListCreateView.as_view(), name="task_list_create"),
    path("api/tasks/<int:pk>/", TaskDetailView.as_view(), name="task_detail"),
    path("api/tasks/board/", TaskBoardView.as_view(), name="task_board"),
    path("api/tasks/calendar/", TaskCalendarView.as_view(), name="task_calendar"),
    path("api/tasks/workload/", TaskWorkloadView.as_view(), name="task_workload"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    # Публичная форма заявки — тот же SPA-бандл, main.tsx решает по пути,
    # какой экран показать (см. apps.core.views.frontend_index).
    path("zayavka/", frontend_index, name="public_request_form"),
]
