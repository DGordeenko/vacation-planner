"""Индивидуальная норма отпуска (ФЗ, п. 7.5)."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from company.models import CompanySettings
from employees.models import Employee
from schedule import calculations
from schedule.tests import seed_rules

URL = "/api/v1/settings/vacation-norms/"


class IndividualNormTests(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()
        self.admin = Employee.objects.create_user(
            email="a@n.ru", employee_number="1", full_name="Админ", hire_date=date(2015, 1, 1))
        self.admin.is_admin = True
        self.admin.save()
        self.user = Employee.objects.create_user(
            email="u@n.ru", employee_number="2", full_name="Сотрудник",
            hire_date=date(2026, 1, 1))

    def post(self, data, who=None):
        self.client.force_authenticate(who or self.admin)
        return self.client.post(URL, data, format="json")

    def test_rate_scales_with_norm(self):
        self.assertEqual(calculations.employee_accrual_rate(self.user), Decimal("2.33"))
        self.user.annual_vacation_norm_override = Decimal("31")
        self.assertEqual(calculations.employee_accrual_rate(self.user), Decimal("2.58"))
        self.assertEqual(calculations.employee_norm(self.user), Decimal("31"))

    def test_override_raises_balance_and_summary_norm(self):
        before = calculations.balance_on(self.user, date(2026, 12, 31))
        self.post({"employee_id": str(self.user.id), "value": 31})
        self.user.refresh_from_db()
        after = calculations.balance_on(self.user, date(2026, 12, 31))
        self.assertEqual(before, Decimal("27.96"))  # 12 × 2,33
        self.assertEqual(after, Decimal("30.96"))  # 12 × 2,58
        self.assertEqual(calculations.annual_summary(self.user, 2026)["annual_norm"], 31)

    def test_admin_sets_and_clears(self):
        response = self.post({"employee_id": str(self.user.id), "value": 35})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["employees"][0]["norm"], 35.0)
        self.assertEqual(response.data["employees"][0]["monthly_rate"], 2.91)
        response = self.post({"employee_id": str(self.user.id), "value": None})
        self.assertEqual(response.data["employees"], [])

    def test_norm_equal_to_company_is_not_an_exception(self):
        response = self.post({"employee_id": str(self.user.id), "value": 28})
        self.assertEqual(response.data["employees"], [])

    def test_bad_values(self):
        for value in (0, 91, 30.5, "много"):
            response = self.post({"employee_id": str(self.user.id), "value": value})
            self.assertEqual(response.status_code, 400, value)

    def test_only_admin(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(URL).status_code, 403)
        response = self.post({"employee_id": str(self.user.id), "value": 40}, who=self.user)
        self.assertEqual(response.status_code, 403)
