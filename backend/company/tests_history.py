"""Настройки во времени: значение действует с месяца, прошлое не меняется."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from company import history
from company.models import CompanySettings, SettingVersion
from employees.models import Employee
from schedule import calculations
from schedule.models import ValidationRule
from schedule.tests import seed_rules


class HistoryBase(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()
        self.admin = Employee.objects.create_user(
            email="a@h.ru", employee_number="1", full_name="Админ", hire_date=date(2015, 1, 1),
            is_admin=True)
        self.user = Employee.objects.create_user(
            email="u@h.ru", employee_number="2", full_name="Сотрудник",
            hire_date=date(2026, 1, 1))
        self.client.force_authenticate(self.admin)

    def patch_settings(self, **data):
        return self.client.patch("/api/v1/settings/", data, format="json")


class CompanySettingsHistoryTests(HistoryBase):
    def test_rate_change_counts_by_months(self):
        self.patch_settings(monthly_accrual_rate="3.00", effective_from="2026-07")
        # 6 месяцев × 2,33 + 6 × 3,00
        self.assertEqual(calculations.balance_on(self.user, date(2026, 12, 31)),
                         Decimal("31.98"))
        self.assertEqual(calculations.balance_on(self.user, date(2026, 6, 30)),
                         Decimal("13.98"))

    def test_future_change_keeps_today(self):
        body = self.patch_settings(annual_vacation_norm_days=31, effective_from="2099-01").json()
        self.assertEqual(body["annual_vacation_norm_days"], 28)
        self.assertEqual([r["value"] for r in body["history"]["annual_vacation_norm_days"]],
                         [28.0, 31.0])
        self.assertEqual(calculations.employee_year_norm(self.user, 2099), 31)
        self.assertEqual(calculations.employee_year_norm(self.user, 2098), 28)

    def test_norm_changed_mid_year_counts_by_months(self):
        self.patch_settings(annual_vacation_norm_days=31, effective_from="2027-07")
        self.assertEqual(calculations.employee_year_norm(self.user, 2027), 30)  # 29,5
        self.assertEqual(calculations.annual_summary(self.user, 2027)["annual_norm"], 30)

    def test_unchanged_value_adds_no_history(self):
        self.patch_settings(annual_vacation_norm_days=28, effective_from="2027-01")
        self.assertFalse(SettingVersion.objects.exists())

    def test_workday_hours_at_day_off_date(self):
        self.patch_settings(workday_hours=7, effective_from="2027-03")
        self.assertEqual(calculations.workday_hours(date(2027, 2, 10)), Decimal("8"))
        self.assertEqual(calculations.workday_hours(date(2027, 3, 10)), Decimal("7"))

    def test_bad_month(self):
        response = self.patch_settings(annual_vacation_norm_days=30, effective_from="март")
        self.assertEqual(response.status_code, 400)

    def test_remove_dated_change(self):
        self.patch_settings(monthly_accrual_rate="3.00", effective_from="2026-07")
        response = self.client.delete(
            "/api/v1/settings/history/?key=monthly_accrual_rate&effective_from=2026-07")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calculations.balance_on(self.user, date(2026, 12, 31)),
                         Decimal("27.96"))

    def test_only_admin_changes_history(self):
        self.client.force_authenticate(self.user)
        response = self.client.delete(
            "/api/v1/settings/history/?key=monthly_accrual_rate&effective_from=2026-07")
        self.assertEqual(response.status_code, 403)


class RuleThresholdHistoryTests(HistoryBase):
    def test_threshold_from_next_year(self):
        response = self.client.patch("/api/v1/validation-rules/min_continuous_days/",
                                     {"threshold_value": "10", "effective_from": "2099-01"},
                                     format="json")
        body = response.json()
        self.assertEqual(body["threshold_value"], "14.00")
        self.assertEqual([r["value"] for r in body["threshold_history"]], [14.0, 10.0])
        self.assertEqual(calculations._rule("min_continuous_days",
                                            date(2099, 1, 1)).threshold_value, Decimal("10"))
        self.assertEqual(calculations._rule("min_continuous_days",
                                            date(2098, 12, 1)).threshold_value, Decimal("14"))
        self.assertEqual(ValidationRule.objects.get(code="min_continuous_days").threshold_value,
                         Decimal("14"))


class EmployeeNormHistoryTests(HistoryBase):
    url = "/api/v1/settings/vacation-norms/"

    def test_own_norm_from_month(self):
        body = self.client.post(self.url, {"employee_id": str(self.user.id), "value": 31,
                                           "effective_from": "2026-07"}, format="json").json()
        self.assertEqual(body["employees"][0]["history"][-1],
                         {"effective_from": "2026-07-01", "value": 31.0})
        # 6 × 2,33 + 6 × 2,58
        self.assertEqual(calculations.balance_on(self.user, date(2026, 12, 31)),
                         Decimal("29.46"))

    def test_set_and_clear_in_same_month_leaves_nothing(self):
        for value in (35, None):
            body = self.client.post(self.url, {"employee_id": str(self.user.id), "value": value,
                                               "effective_from": "2027-01"},
                                    format="json").json()
        self.assertEqual(body["employees"], [])
        self.assertFalse(SettingVersion.objects.exists())

    def test_future_own_norm_listed_before_it_starts(self):
        body = self.client.post(self.url, {"employee_id": str(self.user.id), "value": 31,
                                           "effective_from": "2099-01"}, format="json").json()
        row = body["employees"][0]
        self.assertIsNone(row["norm"])  # сегодня — общая
        self.assertEqual(history.EmployeeRates(self.user).annual_norm(2099), 31)
