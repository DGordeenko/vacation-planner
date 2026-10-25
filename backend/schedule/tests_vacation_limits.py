"""Годовой потолок, длительное отсутствие, черновики в балансе (решение 25.10.2026)."""

from datetime import date

from django.test import TestCase

from company.models import CompanySettings, Holiday
from employees.models import Employee, OrgUnit
from schedule import approvals, calculations
from schedule.models import ScheduleEntry, ValidationRule
from schedule.tests import seed_rules

VACATION = ScheduleEntry.RecordType.VACATION


class LimitsBase(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        group = OrgUnit.objects.create(name="Группа", unit_type=OrgUnit.UnitType.GROUP)
        self.manager = Employee.objects.create_user(
            email="boss@x.ru", employee_number="1", full_name="Руководитель",
            hire_date=date(2015, 1, 1), org_unit=group)
        group.manager = self.manager
        group.save()
        # Принят в 2020 году: накоплено много, баланс проверкам не мешает.
        self.employee = Employee.objects.create_user(
            email="emp@x.ru", employee_number="2", full_name="Сотрудник",
            hire_date=date(2020, 1, 1), org_unit=group)

    def draft(self, start, end, employee=None):
        employee = employee or self.employee
        return ScheduleEntry.objects.create(
            employee=employee, created_by=employee, record_type=VACATION,
            date_from=start, date_to=end)

    def codes(self, start, end, employee=None, exclude=None):
        result = calculations.validate_entry(
            employee or self.employee, start, end, VACATION, exclude_entry_id=exclude)
        return [v.code for v in result.violations]


class AnnualCeilingTests(LimitsBase):
    def fill_42(self):
        # 3 × 14 дней, все с выходными, праздников нет.
        self.draft(date(2027, 3, 1), date(2027, 3, 14))
        self.draft(date(2027, 5, 17), date(2027, 5, 30))
        self.draft(date(2027, 9, 6), date(2027, 9, 19))

    def test_up_to_42_days_allowed(self):
        self.fill_42()
        self.assertEqual(calculations.planned_days_in_year(self.employee, 2027), 42)
        self.assertEqual(calculations.annual_ceiling(self.employee, 2027)[0], 42)

    def test_over_42_days_requires_manager(self):
        """Накоплено много, но больше 42 дней в год — только с подтверждением."""
        self.fill_42()
        result = calculations.validate_entry(
            self.employee, date(2027, 11, 8), date(2027, 11, 14), VACATION)
        violation = next(v for v in result.violations if v.code == "annual_ceiling")
        self.assertTrue(violation.can_be_approved)
        self.assertIn("49 дней при допустимых 42", violation.message)

    def test_small_accumulation_lowers_ceiling(self):
        """Приём 01.08.2026: на 1 января накоплено 11 дней, потолок 28 + 11 = 39."""
        newcomer = Employee.objects.create_user(
            email="new@x.ru", employee_number="3", full_name="Новичок",
            hire_date=date(2026, 8, 1), org_unit=self.employee.org_unit)
        self.assertEqual(calculations.annual_ceiling(newcomer, 2027), (39, 39))
        self.assertEqual(calculations.annual_summary(newcomer, 2027)["ceiling"], 39)

    def test_threshold_is_data(self):
        ValidationRule.objects.filter(code="annual_ceiling").update(threshold_value=30)
        self.draft(date(2027, 3, 1), date(2027, 3, 14))
        self.draft(date(2027, 5, 17), date(2027, 5, 30))
        self.assertIn("annual_ceiling",
                      self.codes(date(2027, 9, 6), date(2027, 9, 12)))


class ContinuousAbsenceTests(LimitsBase):
    def test_week_pieces_joined_over_weekends(self):
        """5 + выходные + 5 + выходные + 3 = 13 рабочих дней — запрос руководителю."""
        self.draft(date(2027, 7, 5), date(2027, 7, 9))
        self.draft(date(2027, 7, 12), date(2027, 7, 16))
        result = calculations.validate_entry(
            self.employee, date(2027, 7, 19), date(2027, 7, 21), VACATION)
        violation = next(v for v in result.violations
                         if v.code == "max_continuous_workdays")
        self.assertIn("05.07.2027—21.07.2027", violation.message)
        self.assertIn("13 рабочих дней", violation.message)

    def test_two_weeks_with_both_weekends_is_fine(self):
        """Сб–вс + 10 рабочих + сб–вс: 16 календарных, но 10 рабочих дней."""
        self.assertNotIn("max_continuous_workdays",
                         self.codes(date(2027, 7, 3), date(2027, 7, 18)))

    def test_eleven_workdays_is_fine_twelve_is_not(self):
        self.assertNotIn("max_continuous_workdays",
                         self.codes(date(2027, 7, 5), date(2027, 7, 19)))  # 11
        self.assertIn("max_continuous_workdays",
                      self.codes(date(2027, 7, 5), date(2027, 7, 20)))     # 12

    def test_holiday_in_gap_joins_vacations(self):
        """Между отпусками выходные и праздничный понедельник — одно отсутствие."""
        Holiday.objects.create(date=date(2027, 5, 10), name="День Победы",
                               day_type=Holiday.DayType.HOLIDAY)
        self.draft(date(2027, 5, 3), date(2027, 5, 7))      # 5 рабочих
        self.assertIn("max_continuous_workdays",            # + 7 = 12 рабочих
                      self.codes(date(2027, 5, 11), date(2027, 5, 19)))

    def test_working_day_in_gap_separates_vacations(self):
        self.draft(date(2027, 7, 5), date(2027, 7, 9))
        self.draft(date(2027, 7, 12), date(2027, 7, 15))  # пятница 16-го рабочая
        self.assertNotIn("max_continuous_workdays",
                         self.codes(date(2027, 7, 17), date(2027, 7, 21)))

    def test_one_approval_covers_whole_absence(self):
        first = self.draft(date(2027, 7, 5), date(2027, 7, 9))
        second = self.draft(date(2027, 7, 12), date(2027, 7, 16))
        third = self.draft(date(2027, 7, 19), date(2027, 7, 21))
        approvals.approve_exception(third, "max_continuous_workdays", self.manager,
                                    "Согласовано, замена есть")
        for entry in (first, second, third):
            self.assertNotIn("max_continuous_workdays",
                             [v.code for v in approvals.unresolved_violations(entry)])


class DraftsSpendBalanceTests(LimitsBase):
    def test_own_drafts_count_against_balance(self):
        """Раньше каждый черновик сверялся с полным балансом — превышение не ограничивалось."""
        newcomer = Employee.objects.create_user(
            email="new@x.ru", employee_number="3", full_name="Новичок",
            hire_date=date(2026, 7, 1), org_unit=self.employee.org_unit)
        self.draft(date(2027, 5, 31), date(2027, 6, 13), employee=newcomer)  # 14 дней
        self.draft(date(2027, 6, 14), date(2027, 6, 20), employee=newcomer)  # 7 дней
        # К 05.07 накоплено около 28 дней, черновики уже взяли 21: осталось ~7.
        # 21 день — превышение около 14 при допустимых 7.
        self.assertIn("balance_overrun",
                      self.codes(date(2027, 7, 5), date(2027, 7, 25), employee=newcomer))
