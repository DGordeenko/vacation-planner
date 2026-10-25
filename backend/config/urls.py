from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from config.health import HealthView
from demo.views import DemoLoginView
from employees.tags import EmployeeSearchView, TagViewSet
from schedule.team import (
    DecisionsView, ExceptionRejectView, TeamCalendarView, TeamEntryDetailView,
)

from audit.views import AuditLogViewSet
from company.views import HolidayAdminViewSet

from employees.views import AuthMeView, InvitationAcceptView, LoginView
from sso.views import (
    AuthMethodsView,
    NegotiateView,
    OidcCallbackView,
    OidcStartView,
)
from schedule.views import (
    AbsenceReasonViewSet,
    AbsenceSummaryView,
    AbsenceViewSet,
    AmendmentActionView,
    DayOffViewSet,
    RvdAdminViewSet,
    TimeOffBalanceView,
    AmendmentView,
    AnnualSummaryView,
    BalanceView,
    BrandingView,
    HolidayListView,
    EntryExceptionView,
    ScheduleApprovalViewSet,
    SubmitYearView,
    NotificationViewSet,
    ScheduleEntryViewSet,
    ValidationRuleViewSet,
)

router = DefaultRouter()
router.register("schedule-entries", ScheduleEntryViewSet, basename="schedule-entry")
router.register("validation-rules", ValidationRuleViewSet, basename="validation-rule")
router.register("notifications", NotificationViewSet, basename="notification")
router.register("admin/holidays", HolidayAdminViewSet, basename="admin-holiday")
router.register("audit-log", AuditLogViewSet, basename="audit-log")
router.register("admin/rvd", RvdAdminViewSet, basename="admin-rvd")
router.register("day-offs", DayOffViewSet, basename="day-off")
router.register("absences", AbsenceViewSet, basename="absence")
router.register("absence-reasons", AbsenceReasonViewSet, basename="absence-reason")
router.register("tags", TagViewSet, basename="tag")
router.register("schedule-approvals", ScheduleApprovalViewSet, basename="schedule-approval")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", HealthView.as_view()),
    path("api/v1/auth/methods/", AuthMethodsView.as_view()),
    path("api/v1/auth/negotiate/", NegotiateView.as_view()),
    path("api/v1/auth/oidc/start/", OidcStartView.as_view()),
    path("api/v1/auth/oidc/callback/", OidcCallbackView.as_view()),
    path("api/v1/auth/login/", LoginView.as_view()),
    path("api/v1/auth/demo-login/", DemoLoginView.as_view()),
    path("api/v1/auth/me/", AuthMeView.as_view()),
    path("api/v1/auth/accept-invitation/", InvitationAcceptView.as_view()),
    path("api/v1/employees/search/", EmployeeSearchView.as_view()),
    path("api/v1/employees/<uuid:employee_id>/balance/", BalanceView.as_view()),
    path("api/v1/employees/<uuid:employee_id>/time-off-balance/",
         TimeOffBalanceView.as_view()),
    path("api/v1/employees/<uuid:employee_id>/absence-summary/",
         AbsenceSummaryView.as_view()),
    path("api/v1/employees/<uuid:employee_id>/annual-summary/", AnnualSummaryView.as_view()),
    path("api/v1/holidays/", HolidayListView.as_view()),
    path("api/v1/settings/branding/", BrandingView.as_view()),
    path("api/v1/employees/<uuid:employee_id>/submit-year/", SubmitYearView.as_view()),
    path("api/v1/employees/<uuid:employee_id>/amendments/", AmendmentView.as_view()),
    path("api/v1/amendments/<uuid:approval_id>/<str:action_name>/",
         AmendmentActionView.as_view()),
    path("api/v1/decisions/", DecisionsView.as_view()),
    path("api/v1/team-calendar/", TeamCalendarView.as_view()),
    path("api/v1/team-calendar/entries/<uuid:entry_id>/", TeamEntryDetailView.as_view()),
    path("api/v1/schedule-entries/<uuid:entry_id>/exception-rejection/",
         ExceptionRejectView.as_view()),
    path("api/v1/schedule-entries/<uuid:entry_id>/exception-approvals/",
         EntryExceptionView.as_view()),
    path("api/v1/", include(router.urls)),
]
