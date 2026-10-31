"""Экран настроек: глобальные настройки, пороги правил, оформление."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from company.models import CompanySettings
from employees.models import Employee
from schedule.models import ValidationRule
from schedule.tests import seed_rules


class SettingsScreenTests(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()
        self.admin = Employee.objects.create_user(
            email="a@s.ru", employee_number="1", full_name="Админ", hire_date=date(2015, 1, 1))
        self.admin.is_admin = True
        self.admin.save()
        self.user = Employee.objects.create_user(
            email="u@s.ru", employee_number="2", full_name="Сотрудник",
            hire_date=date(2015, 1, 1))

    def patch(self, url, data, who=None):
        self.client.force_authenticate(who or self.admin)
        return self.client.patch(url, data, format="json")

    def test_admin_changes_settings(self):
        response = self.patch("/api/v1/settings/", {
            "annual_vacation_norm_days": 31, "monthly_accrual_rate": "2.58",
            "approval_levels": 3, "workday_hours": 7.2,
            "rvd_compensation_tracking_enabled": False})
        self.assertEqual(response.status_code, 200)
        row = CompanySettings.get_solo()
        self.assertEqual(row.annual_vacation_norm_days, Decimal("31"))
        self.assertEqual(row.approval_levels, 3)
        self.assertFalse(row.rvd_compensation_tracking_enabled)

    def test_out_of_range_rejected_and_nothing_saved(self):
        response = self.patch("/api/v1/settings/", {
            "annual_vacation_norm_days": 31, "approval_levels": 9})
        self.assertEqual(response.status_code, 400)
        self.assertIn("approval_levels", response.json()["errors"])
        self.assertEqual(CompanySettings.get_solo().annual_vacation_norm_days, Decimal("28"))

    def test_settings_read_by_all_changed_by_admin_only(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/v1/settings/").status_code, 200)
        self.assertEqual(self.patch("/api/v1/settings/", {"approval_levels": 2},
                                    who=self.user).status_code, 403)

    def test_rule_threshold_validation(self):
        self.assertEqual(self.patch("/api/v1/validation-rules/annual_ceiling/",
                                    {"threshold_value": 45}).status_code, 200)
        self.assertEqual(ValidationRule.objects.get(code="annual_ceiling").threshold_value, 45)
        self.assertEqual(self.patch("/api/v1/validation-rules/annual_ceiling/",
                                    {"threshold_value": -1}).status_code, 400)
        self.assertEqual(self.patch("/api/v1/validation-rules/annual_ceiling/",
                                    {"threshold_value": None}).status_code, 400)
        self.assertEqual(self.patch("/api/v1/validation-rules/no_weekend_captured/",
                                    {"threshold_value": 3}).status_code, 400)
        self.assertEqual(self.patch("/api/v1/validation-rules/no_weekend_captured/",
                                    {"is_enabled": False}).status_code, 200)

    def test_rule_name_not_editable(self):
        self.patch("/api/v1/validation-rules/annual_ceiling/", {"name": "Другое"})
        self.assertNotEqual(ValidationRule.objects.get(code="annual_ceiling").name, "Другое")

    def test_branding_contrast_checked(self):
        bad = self.patch("/api/v1/settings/branding/", {"brand_primary_color": "#FFD400"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("контраст", bad.json()["errors"]["brand_primary_color"][0])
        good = self.patch("/api/v1/settings/branding/",
                          {"brand_primary_color": "#1F4E99", "organization_name": "ООО Ромашка"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["organization_name"], "ООО Ромашка")
        self.assertEqual(self.patch("/api/v1/settings/branding/",
                                    {"organization_name": "  "}).status_code, 400)


class PaginationTests(TestCase):
    def test_page_size_parameter_respected_and_capped(self):
        from company.models import Holiday
        from datetime import timedelta
        user = Employee.objects.create_user(
            email="p@s.ru", employee_number="9", full_name="П", hire_date=date(2015, 1, 1))
        for i in range(60):
            Holiday.objects.create(date=date(2027, 1, 1) + timedelta(days=i), name=f"День {i}")
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(len(client.get("/api/v1/admin/holidays/?page_size=100").json()["results"]), 60)
        self.assertEqual(len(client.get("/api/v1/admin/holidays/").json()["results"]), 50)
