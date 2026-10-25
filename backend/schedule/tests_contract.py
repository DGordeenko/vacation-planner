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
                    "pending_exceptions", "created_at"}
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
DECISIONS_FIELDS = {"count", "approvals", "exceptions"}
APPROVAL_DECISION_FIELDS = {"approval_id", "step_id", "employee_id", "employee_name", "year",
                            "scope", "submitted_at", "planned_days", "entries", "steps"}
TEAM_ENTRY_FIELDS = {"id", "record_type", "date_from", "date_to", "status", "vacation_days"}
EXCEPTION_DECISION_FIELDS = TEAM_ENTRY_FIELDS | {"employee_id", "employee_name", "violations"}
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
