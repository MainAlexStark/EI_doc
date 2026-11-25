from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.catalog.api import FamilyOptionsView, SiTypeSuggestView
from apps.catalog.api_kits import (
    AuxInstrumentDetailView,
    AuxInstrumentsView,
    StandardKitDetailView,
    StandardKitsImportView,
    StandardKitsView,
)
from apps.catalog.api_fif import (
    FifDocumentFileView,
    FifStatusView,
    FifTypeCardView,
    FifTypeLimitsView,
    SiTypeDocumentsView,
)
from apps.catalog.api_types import (
    SiTypeConfirmView,
    SiTypeTemplateJobsView,
    SiTypeDetailView,
    SiTypeListCreateView,
    SiTypePresetsView,
)
from apps.core.api import EmployeeListView, MeView, TelegramLinkCodeView
from apps.notify import api as notify_api
from apps.core.today import TodayView
from apps.core.telegram_views import TelegramWebhookView
from apps.core.views import (
    FRONTEND_ROOT_FILE_URL_RE,
    frontend_index,
    robots_txt,
    sitemap_xml,
    frontend_root_file,
    healthz,
)
from apps.logistics.tiles import TileView
from apps.logistics import api_dispatch
from apps.hub.api import (
    AddressSuggestView,
    VisitorCityView,
    CaptchaConfigView,
    RequestBulkView,
    RequestConfirmView,
    RequestCreateView,
    RequestListView,
    RequestDetailView,
    RequestRejectView,
    RequestRouteView,
)
from apps.hub.api_availability import (
    AvailabilityBulkCreateView,
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
    JournalVerifiersView,
    JournalView,
    NormocontrolScopesView,
    NumberingApplyView,
    NumberingPreviewView,
    SignProtocolsView,
)
from apps.verification.api_field import OrderInstrumentsBulkView, VerificationDetailView, WorkOrderVerificationsView
from apps.verification.api_orders import (
    ClientLookupView,
    ClientSuggestView,
    OrderActView,
    OrderCreateView,
    OrderDetailView,
    OrderIssueView,
)
from apps.verification.api_history import FgisVerificationCardView, InstrumentHistoryView
from apps.verification.api_templates import (
    TemplateApproveView,
    TemplateDetailView,
    TemplateListView,
    TemplatePreviewView,
    VerificationProtocolView,
    VerificationTemplateView,
)
from apps.verification.api_photos import (
    VerificationPhotoDetailView,
    VerificationPhotoImageView,
    VerificationPhotosView,
)
from apps.verification.api_scan import (
    ScanApplyView,
    ScanDetailView,
    ScanImageView,
    WorkOrderBlankView,
    WorkOrderScansView,
)
from apps.verification.api_workorders import (
    WorkOrderCreateView,
    WorkOrderListView,
    WorkOrderDetailView,
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
    # Смена и сброс пароля, настройки уведомлений (apps.notify, как в openseller)
    path("api/auth/password/change/", notify_api.PasswordChangeView.as_view(), name="password_change"),
    path("api/auth/password/forgot/", notify_api.PasswordForgotView.as_view(), name="password_forgot"),
    path("api/auth/password/reset/check/", notify_api.PasswordResetCheckView.as_view(), name="password_reset_check"),
    path("api/auth/password/reset/", notify_api.PasswordResetView.as_view(), name="password_reset"),
    path("api/notify/prefs/", notify_api.PrefsView.as_view(), name="notify_prefs"),
    path("api/notify/test/", notify_api.TestMailView.as_view(), name="notify_test"),
    # Почта фирмы: ящики Timeweb по IMAP/SMTP (apps.mailbox, как «Почта» в openseller)
    path("api/mail/", include("apps.mailbox.urls")),
    path("api/catalog/si-types/suggest/", SiTypeSuggestView.as_view(), name="si_type_suggest"),
    path("api/catalog/si-types/", SiTypeListCreateView.as_view(), name="si_type_list"),
    path("api/catalog/standard-kits/", StandardKitsView.as_view(), name="standard_kits"),
    path("api/catalog/standard-kits/import/", StandardKitsImportView.as_view(), name="standard_kits_import"),
    path("api/catalog/standard-kits/<int:pk>/", StandardKitDetailView.as_view(), name="standard_kit_detail"),
    path("api/catalog/aux-instruments/", AuxInstrumentsView.as_view(), name="aux_instruments"),
    path("api/catalog/aux-instruments/<int:pk>/", AuxInstrumentDetailView.as_view(), name="aux_instrument_detail"),
    path("api/catalog/si-types/presets/", SiTypePresetsView.as_view(), name="si_type_presets"),
    path("api/catalog/si-types/<int:pk>/", SiTypeDetailView.as_view(), name="si_type_detail"),
    path("api/catalog/si-types/<int:pk>/confirm/", SiTypeConfirmView.as_view(), name="si_type_confirm"),
    path("api/catalog/si-types/<int:pk>/template-jobs/", SiTypeTemplateJobsView.as_view(), name="si_type_template_jobs"),
    path("api/catalog/si-types/<int:pk>/documents/", SiTypeDocumentsView.as_view(), name="si_type_documents"),
    path("api/catalog/fif/status/", FifStatusView.as_view(), name="fif_status"),
    path("api/catalog/fif/types/<str:ident>/", FifTypeCardView.as_view(), name="fif_type_card"),
    path("api/catalog/fif/types/<str:ident>/limits/", FifTypeLimitsView.as_view(), name="fif_type_limits"),
    path("api/catalog/fif/documents/<int:pk>/file/", FifDocumentFileView.as_view(), name="fif_document_file"),
    path("api/catalog/families/", FamilyOptionsView.as_view(), name="family_options"),
    path("api/core/employees/", EmployeeListView.as_view(), name="employee_list"),
    path("api/core/employees/me/", MeView.as_view(), name="me"),
    path("api/core/today/", TodayView.as_view(), name="core_today"),
    # Сотрудники и учётные записи — без Django-админки (apps/core/staff.py)
    path("api/core/staff/", api_dispatch.StaffListView.as_view(), name="staff_list"),
    path("api/core/staff/<int:pk>/", api_dispatch.StaffDetailView.as_view(), name="staff_detail"),
    path("api/core/staff/<int:pk>/password/", api_dispatch.StaffPasswordView.as_view(), name="staff_password"),
    # Диспетчерская руководства: панель, карта, командный график, назначение (apps/logistics/dispatch.py)
    path("api/dispatch/overview/", api_dispatch.OverviewView.as_view(), name="dispatch_overview"),
    path("api/dispatch/map/", api_dispatch.MapView.as_view(), name="dispatch_map"),
    path("api/dispatch/schedule/", api_dispatch.ScheduleView.as_view(), name="dispatch_schedule"),
    path("api/dispatch/assign/", api_dispatch.AssignView.as_view(), name="dispatch_assign"),
    path(
        "api/core/employees/me/telegram-link-code/",
        TelegramLinkCodeView.as_view(), name="telegram_link_code",
    ),
    path(
        "api/telegram/webhook/<str:secret>/",
        TelegramWebhookView.as_view(), name="telegram_webhook",
    ),
    path("api/instruments/history/", InstrumentHistoryView.as_view(), name="instrument_history"),
    path(
        "api/instruments/history/fgis/<str:vri_id>/",
        FgisVerificationCardView.as_view(),
        name="fgis_verification_card",
    ),
    path("api/journal/", JournalView.as_view(), name="journal"),
    path("api/journal/export/", JournalExportView.as_view(), name="journal_export"),
    path("api/journal/verifiers/", JournalVerifiersView.as_view(), name="journal_verifiers"),
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
    path("api/verifications/<int:pk>/", VerificationDetailView.as_view(), name="verification_detail"),
    path(
        "api/verifications/<int:pk>/measurements/",
        VerificationMeasurementsView.as_view(), name="verification_measurements",
    ),
    # Шаблоны поверки и протокол поверки (claude/templates.md)
    path("api/verification-templates/", TemplateListView.as_view(), name="verification_templates"),
    path("api/verification-templates/<int:pk>/", TemplateDetailView.as_view(), name="verification_template"),
    path(
        "api/verification-templates/<int:pk>/approve/",
        TemplateApproveView.as_view(), name="verification_template_approve",
    ),
    path(
        "api/verification-templates/<int:pk>/preview.<str:fmt>",
        TemplatePreviewView.as_view(), name="verification_template_preview",
    ),
    path(
        "api/verifications/<int:pk>/template/",
        VerificationTemplateView.as_view(), name="verification_template_for",
    ),
    path(
        "api/verifications/<int:pk>/protocol.<str:fmt>",
        VerificationProtocolView.as_view(), name="verification_protocol",
    ),
    path(
        "api/verifications/<int:pk>/measurements/confirm/",
        VerificationConfirmRowsView.as_view(), name="verification_confirm_rows",
    ),
    # Фото поверяемого СИ (по одной поверке — сколько угодно)
    path(
        "api/verifications/<int:pk>/photos/",
        VerificationPhotosView.as_view(), name="verification_photos",
    ),
    path(
        "api/verification-photos/<int:pk>/",
        VerificationPhotoDetailView.as_view(), name="verification_photo_detail",
    ),
    path(
        "api/verification-photos/<int:pk>/image/",
        VerificationPhotoImageView.as_view(), name="verification_photo_image",
    ),
    # Заявки (EI_Hub)
    path("api/hub/address-suggest/", AddressSuggestView.as_view(), name="address_suggest"),
    path("api/hub/visitor-city/", VisitorCityView.as_view(), name="visitor_city"),
    path("api/hub/captcha-config/", CaptchaConfigView.as_view(), name="captcha_config"),
    path("api/hub/requests/", RequestCreateView.as_view(), name="request_create"),
    path("api/hub/requests/list/", RequestListView.as_view(), name="request_list"),
    path("api/hub/requests/bulk/", RequestBulkView.as_view(), name="request_bulk"),
    path("api/hub/requests/<int:pk>/", RequestDetailView.as_view(), name="request_detail"),
    path("api/hub/requests/<int:pk>/route/", RequestRouteView.as_view(), name="request_route"),
    path("api/hub/requests/<int:pk>/confirm/", RequestConfirmView.as_view(), name="request_confirm"),
    path("api/hub/requests/<int:pk>/reject/", RequestRejectView.as_view(), name="request_reject"),
    # Доступность сотрудников — их собственный календарь + публичные слоты для формы
    path("api/hub/availability/", AvailabilityListCreateView.as_view(), name="availability_list"),
    path("api/hub/availability/bulk/", AvailabilityBulkCreateView.as_view(), name="availability_bulk"),
    path("api/hub/availability/<int:pk>/", AvailabilityDetailView.as_view(), name="availability_detail"),
    path(
        "api/hub/availability/slots/",
        AvailabilityPublicSlotsView.as_view(), name="availability_public_slots",
    ),
    # Наряды
    path("api/work-orders/", WorkOrderListView.as_view(), name="work_order_list"),
    path("api/work-orders/create/", WorkOrderCreateView.as_view(), name="work_order_create"),
    path("api/work-orders/<int:pk>/", WorkOrderDetailView.as_view(), name="work_order_detail"),
    path("api/work-orders/<int:pk>/status/", WorkOrderStatusView.as_view(), name="work_order_status"),
    path(
        "api/work-orders/<int:pk>/verifications/",
        WorkOrderVerificationsView.as_view(), name="work_order_verifications",
    ),
    # Заказы — вкладка «Приёмки» (приборы привезли сами)
    path("api/clients/suggest/", ClientSuggestView.as_view(), name="client_suggest"),
    path("api/clients/lookup/", ClientLookupView.as_view(), name="client_lookup"),
    path("api/orders/", OrderCreateView.as_view(), name="order_create"),
    path("api/orders/<int:pk>/", OrderDetailView.as_view(), name="order_detail"),
    path("api/orders/<int:pk>/issue/", OrderIssueView.as_view(), name="order_issue"),
    path("api/orders/<int:pk>/instruments/bulk/", OrderInstrumentsBulkView.as_view(), name="order_instruments_bulk"),
    path("api/orders/<int:pk>/act/<str:kind>.pdf", OrderActView.as_view(), name="order_act"),
    # Бланк с QR и распознавание (второй срез офлайна, claude/scans.md)
    path(
        "api/work-orders/<int:pk>/blank/",
        WorkOrderBlankView.as_view(), name="work_order_blank",
    ),
    path(
        "api/work-orders/<int:pk>/scans/",
        WorkOrderScansView.as_view(), name="work_order_scans",
    ),
    path("api/scans/<int:pk>/", ScanDetailView.as_view(), name="scan_detail"),
    path("api/scans/<int:pk>/image/", ScanImageView.as_view(), name="scan_image"),
    path("api/scans/<int:pk>/apply/", ScanApplyView.as_view(), name="scan_apply"),
    # Задачи
    path("api/tasks/", TaskListCreateView.as_view(), name="task_list_create"),
    path("api/tasks/<int:pk>/", TaskDetailView.as_view(), name="task_detail"),
    path("api/tasks/board/", TaskBoardView.as_view(), name="task_board"),
    path("api/tasks/calendar/", TaskCalendarView.as_view(), name="task_calendar"),
    path("api/tasks/workload/", TaskWorkloadView.as_view(), name="task_workload"),
    # Ассистент-метролог
    path("api/assistant/", include("apps.assistant.urls")),
    path("api/attestations/", include("apps.equipment.urls")),
    # Командировки и закупки (claude/trips.md)
    path("api/", include("apps.logistics.urls")),
    path("tiles/<int:z>/<int:x>/<int:y>.png", TileView.as_view(), name="map_tile"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    # Публичная форма заявки — тот же SPA-бандл, main.tsx решает по пути,
    # какой экран показать (см. apps.core.views.frontend_index).
    path("zayavka/", frontend_index, name="public_request_form"),
    # Сайт фирмы — две версии с той же формой заявки внутри (main.tsx выбирает по пути):
    # счётчики для населения (/schetchiki/, старый адрес /o-nas/ — та же страница)
    # и метрология для бизнеса (/biznes/).
    path("o-nas/", frontend_index, name="public_landing"),
    path("schetchiki/", frontend_index, name="public_site_meters"),
    path("schetchiki/yaransk/", frontend_index, name="public_site_meters_yaransk"),
    path("biznes/", frontend_index, name="public_site_business"),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap_xml, name="sitemap_xml"),
    # service worker/манифест PWA — обязаны быть в корне, см. frontend_root_file.
    # Последний в списке и только по allowlist (тот же, что FRONTEND_ROOT_FILE_RE):
    # раньше здесь был односегментный catch-all "<str:filename>", и "/o-nas"
    # без слэша попадал в него и получал 404 вместо 301 на "/o-nas/" —
    # APPEND_SLASH срабатывает, только если путь не совпал ни с одним шаблоном.
    re_path(FRONTEND_ROOT_FILE_URL_RE, frontend_root_file, name="frontend_root_file"),
]
