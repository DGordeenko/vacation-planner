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
            {"code", "name", "message", "can_be_approved"}, set(body["violations"][0]))
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
                    "extended_by_holiday_days", "status", "vacation_days", "created_at"}
        self.assertEqual(expected, set(response.json()))

    def test_blocked_creation_returns_violations(self):
        response = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04",
            "record_type": "vacation"}, format="json")
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
