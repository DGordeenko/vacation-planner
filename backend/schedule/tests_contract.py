"""Контракт API для фронтенда.

Тесты фиксируют состав полей, на которые опирается интерфейс
(frontend/src/types.ts). Если поле переименуют или уберут — тест упадёт
здесь, а не в браузере у пользователя.
"""

from datetime import date

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from company.models import CompanySettings, Holiday
from employees.models import Employee, EmployeeTag, OrgUnit, Tag
from schedule.models import ScheduleEntry
from schedule.tests import seed_rules

ME_FIELDS = {"id", "full_name", "email", "employee_number", "hire_date",
             "org_unit", "is_admin", "is_manager", "manager_name", "tags",
             "email_notifications_enabled"}
BRANDING_FIELDS = {"organization_name", "brand_primary_color", "brand_contrast_color",
                   "brand_radius", "brand_font_family", "brand_logo"}
SUMMARY_FIELDS = {"year", "annual_norm", "planned_days", "against_norm",
                  "from_accumulated", "accumulated_stock", "accumulated_stock_exact",
                  "ceiling", "remaining", "longest_continuous",
                  "min_continuous_required", "absence_days", "submitted",
                  "approval_id", "approval_steps", "manager_name", "tags",
                  "year_approved", "amendment_id", "amendment_status"}
VALIDATION_FIELDS = {"is_valid", "violations", "vacation_days",
                     "calendar_days", "holiday_extension"}


class ApiContractTests(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()

        department = OrgUnit.objects.create(
            name="Отдел К", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.chief = Employee.objects.create_user(
            email="chief@k.ru", employee_number="800", full_name="Руководитель",
            hire_date=date(2015, 1, 1), org_unit=department)
        department.manager = self.chief
        department.save()

        group = OrgUnit.objects.create(
            name="Группа К", unit_type=OrgUnit.UnitType.GROUP, parent=department)
        self.employee = Employee.objects.create_user(
            email="user@k.ru", employee_number="801", full_name="Сотрудник",
            hire_date=date(2020, 1, 1), org_unit=group, password="pw12345")

        tag = Tag.objects.create(name="Дежурная смена", max_simultaneous_absence=2)
        EmployeeTag.objects.create(
            employee=self.employee, tag=tag, source=Tag.Source.MANUAL)

        Holiday.objects.create(date=date(2027, 1, 1), name="Новый год",
                               day_type=Holiday.DayType.HOLIDAY)
        self.client.force_authenticate(self.employee)

    def test_me_contract(self):
        body = self.client.get("/api/v1/auth/me/").json()
        self.assertEqual(ME_FIELDS, set(body))
        self.assertEqual(body["manager_name"], "Руководитель")

    def test_branding_contract(self):
        body = self.client.get("/api/v1/settings/branding/").json()
        self.assertEqual(BRANDING_FIELDS, set(body))

    def test_holidays_contract(self):
        body = self.client.get("/api/v1/holidays/?year=2027").json()
        self.assertIsInstance(body, list)
        self.assertEqual({"date", "name", "day_type"}, set(body[0]))

    def test_annual_summary_contract(self):
        body = self.client.get(
            f"/api/v1/employees/{self.employee.id}/annual-summary/?year=2027").json()
        self.assertEqual(SUMMARY_FIELDS, set(body))
        self.assertEqual(
            {"tag_id", "name", "limit", "source", "colleagues_on_leave"},
            set(body["tags"][0]))

    def test_validate_contract(self):
        body = self.client.post("/api/v1/schedule-entries/validate/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04",
            "record_type": "vacation"}, format="json").json()
        self.assertEqual(VALIDATION_FIELDS, set(body))
        self.assertEqual(
            {"code", "name", "message", "can_be_approved", "colleagues"},
            set(body["violations"][0]))
        self.assertEqual(
            {"suggested_date_to", "added_days"}, set(body["holiday_extension"]))

    def test_entry_contract(self):
        response = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-07-05", "date_to": "2027-07-18",
            "record_type": "vacation"}, format="json")
        self.assertEqual(response.status_code, 201)
        expected = {"id", "employee", "employee_name", "record_type", "date_from",
                    "date_to", "reason", "custom_reason_text", "task_link",
                    "overtime_hours", "rvd_compensation_type",
                    "extended_by_holiday_days", "status", "vacation_days",
                    "pending_exceptions", "rejection_comment", "created_at"}
        self.assertEqual(expected, set(response.json()))

    def test_blocked_creation_returns_violations(self):
        """Не подлежащее подтверждению нарушение (пересечение со своим
        отпуском) — 409 со списком нарушений."""
        payload = {"date_from": "2027-03-05", "date_to": "2027-03-08",
                   "record_type": "vacation"}
        self.client.post("/api/v1/schedule-entries/", payload, format="json")
        response = self.client.post("/api/v1/schedule-entries/", payload, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertIn("violations", response.json())

    def test_entries_list_is_paginated(self):
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 5), date_to=date(2027, 7, 18))
        body = self.client.get(
            f"/api/v1/schedule-entries/?employee_id={self.employee.id}"
            "&date_from=2027-01-01&date_to=2027-12-31").json()
        self.assertEqual({"count", "next", "previous", "results"}, set(body))

    def test_notifications_contract(self):
        body = self.client.get("/api/v1/notifications/").json()
        self.assertEqual({"count", "next", "previous", "results"}, set(body))

    def test_branding_colour_is_validated(self):
        self.employee.is_admin = True
        self.employee.save()
        bad = self.client.patch("/api/v1/settings/branding/",
                                {"brand_primary_color": "синий"}, format="json")
        self.assertEqual(bad.status_code, 400)
        good = self.client.patch("/api/v1/settings/branding/",
                                 {"brand_primary_color": "#123456"}, format="json")
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["brand_primary_color"], "#123456")


AUTH_METHODS_FIELDS = {"password_login", "providers", "demo"}
DEMO_METHODS_FIELDS = {"enabled", "roles"}
DEMO_ROLE_FIELDS = {"code", "title", "description", "full_name"}
DEMO_LOGIN_FIELDS = {"token", "employee_id", "scheme", "role"}


@override_settings(DEMO_MODE_ENABLED=True)
class DemoContractTests(TestCase):
    """Экран входа и вход в демо (types.ts: AuthMethods, DemoRole, DemoLogin)."""

    databases = {"default", "demo"}

    def setUp(self):
        from demo.data import ROLES
        from demo.db import use_demo_database
        with use_demo_database():
            for role in ROLES:
                Employee.objects.create_user(
                    email=f"{role.code}@test-corp.example",
                    employee_number=role.employee_number, full_name=role.title,
                    hire_date=date(2020, 1, 1))
        self.client = APIClient()

    def test_auth_methods_contract(self):
        body = self.client.get("/api/v1/auth/methods/").json()
        self.assertEqual(AUTH_METHODS_FIELDS, set(body))
        self.assertEqual(DEMO_METHODS_FIELDS, set(body["demo"]))
        self.assertEqual(DEMO_ROLE_FIELDS, set(body["demo"]["roles"][0]))

    def test_demo_login_contract(self):
        body = self.client.post("/api/v1/auth/demo-login/", {"role": "manager"},
                                format="json").json()
        self.assertEqual(DEMO_LOGIN_FIELDS, set(body))


# types.ts: Decisions, ApprovalDecision, ExceptionDecision, TeamEntry
DECISIONS_FIELDS = {"count", "approvals", "exceptions", "day_offs"}
APPROVAL_DECISION_FIELDS = {"approval_id", "step_id", "employee_id", "employee_name", "year",
                            "scope", "submitted_at", "planned_days", "entries", "steps"}
TEAM_ENTRY_FIELDS = {"id", "record_type", "date_from", "date_to", "status", "vacation_days"}
EXCEPTION_DECISION_FIELDS = TEAM_ENTRY_FIELDS | {"employee_id", "employee_name", "violations"}
DAY_OFF_DECISION_FIELDS = TEAM_ENTRY_FIELDS | {"employee_id", "employee_name", "balance"}
TIME_OFF_SUMMARY_FIELDS = {"hours", "workday_hours", "available_days", "remainder_hours"}
APPROVAL_STEP_FIELDS = {"id", "order", "level", "level_display", "approver", "approver_name",
                        "status", "comment", "decided_at"}
# types.ts: TeamCalendar, TagRow, TagMember, EmployeeBrief
TEAM_CALENDAR_FIELDS = {"date_from", "date_to", "scope", "can_view_all", "tag",
                        "limit_reached", "rows"}
TEAM_ROW_FIELDS = {"employee_id", "full_name", "org_unit", "own", "entries"}
TEAM_CALENDAR_ENTRY_FIELDS = TEAM_ENTRY_FIELDS | {"waiting_for"}
TEAM_ENTRY_DETAIL_FIELDS = TEAM_ENTRY_FIELDS | {"employee_name", "org_unit", "steps",
                                                "exception"}
TAG_FIELDS = {"id", "name", "max_simultaneous_absence", "members_count", "manual_count",
              "is_org_unit_tag"}
TAG_MEMBER_FIELDS = {"employee_id", "full_name", "employee_number", "org_unit", "source",
                     "assigned_by", "assigned_at"}
EMPLOYEE_BRIEF_FIELDS = {"id", "full_name", "employee_number", "org_unit"}


class ManagerScreensContractTests(TestCase):
    def setUp(self):
        from schedule import approvals
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()
        unit = OrgUnit.objects.create(name="Отдел М", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.chief = Employee.objects.create_user(
            email="m@m.ru", employee_number="700", full_name="Руководитель",
            hire_date=date(2015, 1, 1), org_unit=unit)
        unit.manager = self.chief
        unit.save()
        self.employee = Employee.objects.create_user(
            email="e@m.ru", employee_number="701", full_name="Сотрудник",
            hire_date=date(2018, 1, 1), org_unit=unit)
        self.tag = Tag.objects.create(name="Дежурство", max_simultaneous_absence=1)
        EmployeeTag.objects.create(employee=self.employee, tag=self.tag,
                                   source=Tag.Source.MANUAL)
        for start, end in [(date(2027, 7, 5), date(2027, 7, 18)),
                           (date(2027, 9, 6), date(2027, 9, 19))]:
            ScheduleEntry.objects.create(
                employee=self.employee, created_by=self.employee,
                record_type=ScheduleEntry.RecordType.VACATION, date_from=start, date_to=end)
        approvals.submit_year(self.employee, 2027, self.employee)
        self.client.force_authenticate(self.employee)
        self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04", "record_type": "vacation"},
            format="json")
        self.client.force_authenticate(self.chief)

    def test_decisions_contract(self):
        body = self.client.get("/api/v1/decisions/").json()
        self.assertEqual(DECISIONS_FIELDS, set(body))
        self.assertEqual(APPROVAL_DECISION_FIELDS, set(body["approvals"][0]))
        self.assertEqual(TEAM_ENTRY_FIELDS, set(body["approvals"][0]["entries"][0]))
        self.assertEqual(APPROVAL_STEP_FIELDS, set(body["approvals"][0]["steps"][0]))
        self.assertEqual(EXCEPTION_DECISION_FIELDS, set(body["exceptions"][0]))

    def test_team_calendar_contract(self):
        body = self.client.get(
            f"/api/v1/team-calendar/?date_from=2027-07-01&date_to=2027-07-31"
            f"&tag_id={self.tag.id}").json()
        self.assertEqual(TEAM_CALENDAR_FIELDS, set(body))
        self.assertEqual({"id", "name", "limit"}, set(body["tag"]))
        self.assertEqual({"date", "count"}, set(body["limit_reached"][0]))
        self.assertEqual(TEAM_ROW_FIELDS, set(body["rows"][0]))
        entry = body["rows"][0]["entries"][0]
        self.assertEqual(TEAM_CALENDAR_ENTRY_FIELDS, set(entry))
        detail = self.client.get(f"/api/v1/team-calendar/entries/{entry['id']}/").json()
        self.assertEqual(TEAM_ENTRY_DETAIL_FIELDS, set(detail))
        self.assertEqual(APPROVAL_STEP_FIELDS | {"is_current"}, set(detail["steps"][0]))

    def test_tags_contract(self):
        self.assertEqual(TAG_FIELDS, set(self.client.get("/api/v1/tags/").json()[0]))
        self.assertEqual(TAG_MEMBER_FIELDS, set(
            self.client.get(f"/api/v1/tags/{self.tag.id}/members/").json()[0]))
        self.assertEqual(EMPLOYEE_BRIEF_FIELDS, set(
            self.client.get("/api/v1/employees/search/?q=Сотр").json()[0]))


# types.ts: EmployeeImportReport, ImportProblem
EMPLOYEE_IMPORT_FIELDS = {"applied", "dry_run", "total_rows", "created", "updated",
                          "problems", "warnings"}


class EmployeeImportContractTests(TestCase):
    def test_import_report_contract(self):
        import base64
        CompanySettings.get_solo()
        admin = Employee.objects.create_user(
            email="a@i.ru", employee_number="1", full_name="А", hire_date=date(2015, 1, 1))
        admin.is_admin = True
        admin.save()
        client = APIClient()
        client.force_authenticate(admin)
        text = "employee_number;full_name;email;hire_date\n5;Б;;2015-01-01\n"
        body = client.post("/api/v1/employees/import-csv/", {
            "content": base64.b64encode(text.encode()).decode(), "dry_run": True},
            format="json").json()
        self.assertEqual(EMPLOYEE_IMPORT_FIELDS, set(body))
        self.assertEqual({"row", "column", "value", "message"}, set(body["problems"][0]))


# types.ts: Invitation
INVITATION_FIELDS = {"employee_id", "full_name", "employee_number", "email", "org_unit",
                     "invitation_token"}


class InvitationContractTests(TestCase):
    def test_invitation_contract(self):
        import uuid
        admin = Employee.objects.create_user(
            email="a@i.ru", employee_number="1", full_name="А", hire_date=date(2015, 1, 1))
        admin.is_admin = True
        admin.save()
        Employee.objects.create_user(
            email="b@i.ru", employee_number="2", full_name="Б", hire_date=date(2015, 1, 1),
            invitation_token=uuid.uuid4())
        client = APIClient()
        client.force_authenticate(admin)
        rows = client.get("/api/v1/admin/invitations/").json()
        self.assertEqual(INVITATION_FIELDS, set(rows[0]))


# types.ts: AuditRow, AuditChange, AuditEntityType
AUDIT_ROW_FIELDS = {"id", "entity_type", "entity_type_display", "entity_id", "entity_label",
                    "action", "action_display", "changed_by", "changed_by_name",
                    "subject_employee", "subject_name", "changed_at", "diff", "changes",
                    "source"}


class AuditContractTests(TestCase):
    def test_audit_contract(self):
        from audit.context import acting_as
        from company.models import Holiday
        admin = Employee.objects.create_user(
            email="a@i.ru", employee_number="1", full_name="А", hire_date=date(2015, 1, 1))
        admin.is_admin = True
        admin.save()
        with acting_as(admin, source="api"):
            Holiday.objects.create(date=date(2027, 1, 7), name="Рождество")
        client = APIClient()
        client.force_authenticate(admin)
        body = client.get("/api/v1/audit-log/?entity_type=Holiday").json()
        self.assertEqual({"count", "next", "previous", "results"}, set(body))
        self.assertEqual(AUDIT_ROW_FIELDS, set(body["results"][0]))
        self.assertEqual({"field", "label", "was", "became"},
                         set(body["results"][0]["changes"][0]))
        self.assertEqual({"code", "name"},
                         set(client.get("/api/v1/audit-log/entity-types/").json()[0]))


# types.ts: CompanySettingsRow, ValidationRuleRow
COMPANY_SETTINGS_FIELDS = {"annual_vacation_norm_days", "monthly_accrual_rate",
                           "approval_levels", "workday_hours",
                           "rvd_compensation_tracking_enabled"}
VALIDATION_RULE_FIELDS = {"code", "name", "description", "is_enabled", "threshold_value",
                          "approver_level"}


class SettingsContractTests(TestCase):
    def test_settings_and_rules_contract(self):
        from schedule.tests import seed_rules
        CompanySettings.get_solo()
        seed_rules()
        user = Employee.objects.create_user(
            email="a@i.ru", employee_number="1", full_name="А", hire_date=date(2015, 1, 1))
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(COMPANY_SETTINGS_FIELDS, set(client.get("/api/v1/settings/").json()))
        rules = client.get("/api/v1/validation-rules/").json()
        rows = rules["results"] if isinstance(rules, dict) else rules
        self.assertEqual(VALIDATION_RULE_FIELDS, set(rows[0]))


# types.ts: TimeOffBalance, DayOffDecision, Absence, AbsenceSummary, AbsenceReason
ABSENCE_FIELDS = {"id", "employee", "employee_name", "date_from", "date_to", "reason",
                  "reason_name", "custom_reason_text", "days", "recorded_by",
                  "recorded_by_id", "created_at"}


class TimeOffContractTests(TestCase):
    def test_time_off_contract(self):
        from decimal import Decimal
        from company.models import AbsenceReason
        from employees.models import OrgUnit
        from schedule import calculations
        CompanySettings.get_solo()
        unit = OrgUnit.objects.create(name="О", unit_type=OrgUnit.UnitType.DEPARTMENT)
        chief = Employee.objects.create_user(
            email="c@t.ru", employee_number="1", full_name="Ч", hire_date=date(2015, 1, 1),
            org_unit=unit)
        unit.manager = chief
        unit.save()
        emp = Employee.objects.create_user(
            email="e@t.ru", employee_number="2", full_name="Е", hire_date=date(2015, 1, 1),
            org_unit=unit)
        rvd = ScheduleEntry.objects.create(
            employee=emp, created_by=chief, record_type=ScheduleEntry.RecordType.RVD,
            date_from=date(2026, 3, 7), date_to=date(2026, 3, 7), work_hours=Decimal("8"),
            rvd_compensation_type=ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF,
            status=ScheduleEntry.Status.APPROVED)
        calculations.sync_rvd_accrual(rvd)
        client = APIClient()
        client.force_authenticate(emp)
        balance = client.get(f"/api/v1/employees/{emp.id}/time-off-balance/").json()
        self.assertEqual(TIME_OFF_SUMMARY_FIELDS | {"pending_days", "free_days", "history"},
                         set(balance))
        self.assertEqual({"date", "hours", "reason", "comment"}, set(balance["history"][0]))
        client.post("/api/v1/day-offs/", {"date_from": "2027-03-10"}, format="json")
        sick = AbsenceReason.objects.get(code="sick_leave")
        client.post("/api/v1/absences/", {"date_from": "2026-03-10", "reason": str(sick.id)},
                    format="json")
        self.assertEqual(ABSENCE_FIELDS, set(client.get("/api/v1/absences/").json()["results"][0]))
        self.assertEqual({"year", "total_days", "episodes", "by_reason"}, set(
            client.get(f"/api/v1/employees/{emp.id}/absence-summary/?year=2026").json()))
        self.assertEqual({"id", "code", "name", "is_other"},
                         set(client.get("/api/v1/absence-reasons/").json()["results"][0]))
        client.force_authenticate(chief)
        decisions = client.get("/api/v1/decisions/").json()
        self.assertEqual(DAY_OFF_DECISION_FIELDS, set(decisions["day_offs"][0]))
        self.assertEqual(TIME_OFF_SUMMARY_FIELDS, set(decisions["day_offs"][0]["balance"]))


# types.ts: RvdRow
RVD_FIELDS = {"id", "employee", "employee_name", "employee_number", "date_from", "work_hours",
              "rvd_compensation_type", "compensation_display", "task_link"}


class RvdContractTests(TestCase):
    def test_rvd_contract(self):
        CompanySettings.get_solo()
        admin = Employee.objects.create_user(
            email="a@r.ru", employee_number="1", full_name="А", hire_date=date(2015, 1, 1))
        admin.is_admin = True
        admin.save()
        client = APIClient()
        client.force_authenticate(admin)
        created = client.post("/api/v1/admin/rvd/", {
            "employee": str(admin.id), "date_from": "2026-10-24", "work_hours": "8",
            "rvd_compensation_type": "pay_plus_day_off"}, format="json").json()
        self.assertEqual(RVD_FIELDS, set(created))
        listed = client.get("/api/v1/admin/rvd/?year=2026&month=10").json()
        self.assertEqual(RVD_FIELDS, set(listed["results"][0]))
