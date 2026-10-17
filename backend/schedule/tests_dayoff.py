"""Отгулы за работу в выходные дни."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from company.models import CompanySettings, Holiday
from employees.models import Employee, EmployeeTag, OrgUnit, Tag
from schedule import calculations
from schedule.models import Notification, ScheduleEntry, TimeOffLedger
from schedule.tests import seed_rules


class DayOffBase(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()

        self.department = OrgUnit.objects.create(
            name="Отдел Р", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.chief = Employee.objects.create_user(
            email="chief@r.ru", employee_number="R0", full_name="Руководитель",
            hire_date=date(2015, 1, 1), org_unit=self.department)
        self.department.manager = self.chief
        self.department.save()

        self.group = OrgUnit.objects.create(
            name="Группа Р", unit_type=OrgUnit.UnitType.GROUP, parent=self.department)
        self.employee = Employee.objects.create_user(
            email="emp@r.ru", employee_number="R1", full_name="Сотрудник",
            hire_date=date(2020, 1, 1), org_unit=self.group, password="pw12345")

        self.admin = Employee.objects.create_user(
            email="adm@r.ru", employee_number="R2", full_name="Админ",
            hire_date=date(2016, 1, 1), org_unit=self.department)
        self.admin.is_admin = True
        self.admin.save()

    def add_rvd(self, day, hours, compensation, employee=None):
        entry = ScheduleEntry.objects.create(
            employee=employee or self.employee, created_by=self.admin,
            record_type=ScheduleEntry.RecordType.RVD,
            date_from=day, date_to=day,
            work_hours=Decimal(str(hours)),
            rvd_compensation_type=compensation,
            status=ScheduleEntry.Status.APPROVED)
        calculations.sync_rvd_accrual(entry)
        return entry

    def balance(self, employee=None):
        return calculations.time_off_balance_hours(employee or self.employee)


class AccrualTests(DayOffBase):
    def test_pay_plus_day_off_accrues_hours(self):
        self.add_rvd(date(2027, 3, 6), 8,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.assertEqual(self.balance(), Decimal("8.00"))

    def test_double_pay_accrues_nothing(self):
        self.add_rvd(date(2027, 3, 6), 8,
                     ScheduleEntry.RvdCompensation.DOUBLE_PAY)
        self.assertEqual(self.balance(), Decimal("0.00"))
        self.assertFalse(TimeOffLedger.objects.exists())

    def test_partial_day_accrues_exact_hours(self):
        """РВД на 4 часа даёт 4 часа, а не половину дня и не полный день."""
        self.add_rvd(date(2027, 3, 6), 4,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.assertEqual(self.balance(), Decimal("4.00"))
        summary = calculations.time_off_summary(self.employee)
        self.assertEqual(summary["available_days"], 0)
        self.assertEqual(summary["remainder_hours"], 4.0)

    def test_partial_days_accumulate_into_full_day(self):
        """Два РВД по 4 часа дают один полный отгул."""
        self.add_rvd(date(2027, 3, 6), 4,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.add_rvd(date(2027, 3, 13), 4.5,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        summary = calculations.time_off_summary(self.employee)
        self.assertEqual(summary["hours"], 8.5)
        self.assertEqual(summary["available_days"], 1)
        self.assertEqual(summary["remainder_hours"], 0.5)

    def test_editing_hours_updates_accrual(self):
        """Администратор исправил часы — начисление меняется, а не дублируется."""
        entry = self.add_rvd(date(2027, 3, 6), 8,
                             ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        entry.work_hours = Decimal("6")
        entry.save()
        calculations.sync_rvd_accrual(entry)
        self.assertEqual(self.balance(), Decimal("6.00"))
        self.assertEqual(TimeOffLedger.objects.count(), 1)

    def test_switching_to_double_pay_removes_accrual(self):
        entry = self.add_rvd(date(2027, 3, 6), 8,
                             ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        entry.rvd_compensation_type = ScheduleEntry.RvdCompensation.DOUBLE_PAY
        entry.save()
        calculations.sync_rvd_accrual(entry)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_deleting_rvd_removes_accrual(self):
        entry = self.add_rvd(date(2027, 3, 6), 8,
                             ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        entry.delete()
        self.assertEqual(self.balance(), Decimal("0.00"))


class TakingDayOffTests(DayOffBase):
    def setUp(self):
        super().setUp()
        self.add_rvd(date(2027, 3, 6), 8,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)

    def test_blocked_without_balance(self):
        TimeOffLedger.objects.all().delete()
        result = calculations.validate_day_off(self.employee, date(2027, 4, 7))
        codes = [v.code for v in result.violations]
        self.assertIn("day_off_no_balance", codes)
        self.assertFalse(
            [v for v in result.violations
             if v.code == "day_off_no_balance"][0].can_be_approved)

    def test_allowed_with_balance(self):
        result = calculations.validate_day_off(self.employee, date(2027, 4, 7))
        self.assertTrue(result.is_valid, [v.message for v in result.violations])

    def test_weekend_is_pointless(self):
        result = calculations.validate_day_off(self.employee, date(2027, 4, 10))
        self.assertIn("day_off_on_rest_day", [v.code for v in result.violations])

    def test_holiday_is_pointless(self):
        Holiday.objects.create(date=date(2027, 5, 3), name="Праздник",
                               day_type=Holiday.DayType.HOLIDAY)
        result = calculations.validate_day_off(self.employee, date(2027, 5, 3))
        self.assertIn("day_off_on_rest_day", [v.code for v in result.violations])

    def test_overlap_with_vacation_blocked(self):
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 4, 5), date_to=date(2027, 4, 16),
            status=ScheduleEntry.Status.APPROVED)
        result = calculations.validate_day_off(self.employee, date(2027, 4, 7))
        self.assertIn("self_overlap", [v.code for v in result.violations])

    def test_second_day_off_blocked_on_single_day_balance(self):
        """Второй отгул при 8 часах нельзя — первый уже занял баланс."""
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.DAY_OFF,
            date_from=date(2027, 4, 7), date_to=date(2027, 4, 7),
            status=ScheduleEntry.Status.PENDING_APPROVAL)
        result = calculations.validate_day_off(self.employee, date(2027, 4, 8))
        self.assertIn("day_off_no_balance", [v.code for v in result.violations])

    def test_tag_limit_applies_to_day_offs(self):
        """Отгул — тоже отсутствие, лимит по тегу его учитывает."""
        tag = Tag.objects.create(name="Дежурная смена", max_simultaneous_absence=1)
        EmployeeTag.objects.create(employee=self.employee, tag=tag,
                                   source=Tag.Source.MANUAL)
        colleague = Employee.objects.create_user(
            email="c@r.ru", employee_number="R3", full_name="Коллега",
            hire_date=date(2020, 1, 1), org_unit=self.group)
        EmployeeTag.objects.create(employee=colleague, tag=tag,
                                   source=Tag.Source.MANUAL)
        ScheduleEntry.objects.create(
            employee=colleague, created_by=colleague,
            record_type=ScheduleEntry.RecordType.DAY_OFF,
            date_from=date(2027, 4, 7), date_to=date(2027, 4, 7),
            status=ScheduleEntry.Status.APPROVED)

        result = calculations.validate_day_off(self.employee, date(2027, 4, 7))
        self.assertTrue(any(v.code.startswith("tag_") or v.code == "tag_limit_exceeded"
                            for v in result.violations))


class DayOffApiTests(DayOffBase):
    def test_admin_enters_rvd_for_month(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/rvd/bulk/", {
            "rows": [
                {"employee": str(self.employee.id), "date_from": "2027-03-06",
                 "work_hours": "8", "rvd_compensation_type": "pay_plus_day_off",
                 "task_link": "https://tracker/TASK-1"},
                {"employee": str(self.employee.id), "date_from": "2027-03-13",
                 "work_hours": "4", "rvd_compensation_type": "double_pay"},
            ]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.balance(), Decimal("8.00"))

    def test_employee_cannot_enter_rvd(self):
        self.client.force_authenticate(self.employee)
        response = self.client.post("/api/v1/admin/rvd/", {
            "employee": str(self.employee.id), "date_from": "2027-03-06",
            "work_hours": "8", "rvd_compensation_type": "pay_plus_day_off"},
            format="json")
        self.assertEqual(response.status_code, 403)

    def test_hours_must_be_positive(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/rvd/", {
            "employee": str(self.employee.id), "date_from": "2027-03-06",
            "work_hours": "0", "rvd_compensation_type": "pay_plus_day_off"},
            format="json")
        self.assertEqual(response.status_code, 400)

    def test_balance_endpoint(self):
        self.add_rvd(date(2027, 3, 6), 12,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.client.force_authenticate(self.employee)
        body = self.client.get(
            f"/api/v1/employees/{self.employee.id}/time-off-balance/").json()
        self.assertEqual(body["hours"], 12.0)
        self.assertEqual(body["available_days"], 1)
        self.assertEqual(body["remainder_hours"], 4.0)
        self.assertEqual(len(body["history"]), 1)

    def test_full_day_off_cycle(self):
        self.add_rvd(date(2027, 3, 6), 8,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.client.force_authenticate(self.employee)

        created = self.client.post("/api/v1/day-offs/",
                                   {"date_from": "2027-04-07"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["status"], "pending_approval")
        # Баланс списывается только после согласования
        self.assertEqual(self.balance(), Decimal("8.00"))

        self.assertTrue(Notification.objects.filter(recipient=self.chief).exists())

        self.client.force_authenticate(self.chief)
        approved = self.client.post(
            f"/api/v1/day-offs/{created.json()['id']}/approve/")
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_rejected_day_off_does_not_spend_balance(self):
        self.add_rvd(date(2027, 3, 6), 8,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.client.force_authenticate(self.employee)
        created = self.client.post("/api/v1/day-offs/",
                                   {"date_from": "2027-04-07"}, format="json")
        self.client.force_authenticate(self.chief)
        rejected = self.client.post(
            f"/api/v1/day-offs/{created.json()['id']}/reject/",
            {"comment": "Нужен на проекте"}, format="json")
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(self.balance(), Decimal("8.00"))

    def test_rejection_requires_comment(self):
        self.add_rvd(date(2027, 3, 6), 8,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.client.force_authenticate(self.employee)
        created = self.client.post("/api/v1/day-offs/",
                                   {"date_from": "2027-04-07"}, format="json")
        self.client.force_authenticate(self.chief)
        response = self.client.post(
            f"/api/v1/day-offs/{created.json()['id']}/reject/", {}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_stranger_cannot_approve(self):
        self.add_rvd(date(2027, 3, 6), 8,
                     ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF)
        self.client.force_authenticate(self.employee)
        created = self.client.post("/api/v1/day-offs/",
                                   {"date_from": "2027-04-07"}, format="json")
        stranger = Employee.objects.create_user(
            email="s@r.ru", employee_number="R9", full_name="Чужой",
            hire_date=date(2020, 1, 1), org_unit=self.group)
        self.client.force_authenticate(stranger)
        response = self.client.post(
            f"/api/v1/day-offs/{created.json()['id']}/approve/")
        self.assertIn(response.status_code, (403, 404))

    def test_creation_blocked_without_balance(self):
        TimeOffLedger.objects.all().delete()
        self.client.force_authenticate(self.employee)
        response = self.client.post("/api/v1/day-offs/",
                                    {"date_from": "2027-04-07"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "day_off_blocked")

    def test_validate_endpoint(self):
        self.client.force_authenticate(self.employee)
        body = self.client.post("/api/v1/day-offs/validate/",
                                {"date": "2027-04-10"}, format="json").json()
        self.assertFalse(body["is_valid"])
        self.assertIn("balance", body)


class TaskLinkTests(DayOffBase):
    """Ссылки на внутренние трекеры без домена верхнего уровня."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)

    def _post(self, link):
        return self.client.post("/api/v1/admin/rvd/", {
            "employee": str(self.employee.id), "date_from": "2027-03-06",
            "work_hours": "8", "rvd_compensation_type": "pay_plus_day_off",
            "task_link": link}, format="json")

    def test_internal_host_accepted(self):
        self.assertEqual(self._post("https://jira/browse/TASK-1").status_code, 201)

    def test_external_url_accepted(self):
        self.assertEqual(
            self._post("https://tracker.corp.ru/browse/TASK-1").status_code, 201)

    def test_garbage_rejected(self):
        self.assertEqual(self._post("просто текст").status_code, 400)

    def test_empty_allowed(self):
        self.assertEqual(self._post("").status_code, 201)
