"""Внеплановые отсутствия."""

from datetime import date, timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from company.models import AbsenceReason, CompanySettings, Holiday
from employees.models import Employee, EmployeeTag, OrgUnit, Tag
from schedule import calculations
from schedule.models import Notification, ScheduleEntry
from schedule.tests import seed_rules


class AbsenceBase(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()

        self.department = OrgUnit.objects.create(
            name="Отдел А", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.chief = Employee.objects.create_user(
            email="chief@a.ru", employee_number="A0", full_name="Руководитель",
            hire_date=date(2015, 1, 1), org_unit=self.department)
        self.department.manager = self.chief
        self.department.save()

        self.group = OrgUnit.objects.create(
            name="Группа А", unit_type=OrgUnit.UnitType.GROUP, parent=self.department)
        self.employee = Employee.objects.create_user(
            email="emp@a.ru", employee_number="A1", full_name="Сотрудник",
            hire_date=date(2020, 1, 1), org_unit=self.group, password="pw12345")

        self.admin = Employee.objects.create_user(
            email="adm@a.ru", employee_number="A2", full_name="Админ",
            hire_date=date(2016, 1, 1), org_unit=self.department)
        self.admin.is_admin = True
        self.admin.save()

        # Справочник создаёт миграция company/0004
        self.sick = AbsenceReason.objects.get(code="sick_leave")
        self.other = AbsenceReason.objects.get(code="other")

        self.yesterday = date.today() - timedelta(days=1)
        self.week_ago = date.today() - timedelta(days=7)

    def record(self, start, end, reason=None, text="", employee=None):
        return ScheduleEntry.objects.create(
            employee=employee or self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.ABSENCE,
            date_from=start, date_to=end,
            reason=reason or self.sick, custom_reason_text=text,
            status=ScheduleEntry.Status.APPROVED)


class ValidationTests(AbsenceBase):
    def test_valid_past_absence(self):
        result = calculations.validate_absence(
            self.employee, self.week_ago, self.yesterday, self.sick)
        self.assertTrue(result.is_valid, [v.message for v in result.violations])

    def test_reason_is_required(self):
        result = calculations.validate_absence(
            self.employee, self.week_ago, self.yesterday, None)
        codes = [v.code for v in result.violations]
        self.assertIn("reason_required", codes)

    def test_other_requires_text(self):
        result = calculations.validate_absence(
            self.employee, self.week_ago, self.yesterday, self.other, "")
        self.assertIn("custom_reason_required", [v.code for v in result.violations])

    def test_other_with_text_is_valid(self):
        result = calculations.validate_absence(
            self.employee, self.week_ago, self.yesterday, self.other,
            "Вызов в военкомат")
        self.assertTrue(result.is_valid)

    def test_future_absence_needs_confirmation(self):
        """Отсутствие фиксируется по факту, будущее — повод переспросить."""
        future = date.today() + timedelta(days=5)
        result = calculations.validate_absence(
            self.employee, future, future, self.sick)
        violation = [v for v in result.violations if v.code == "absence_in_future"]
        self.assertTrue(violation)
        self.assertTrue(violation[0].can_be_approved)

    def test_reversed_period_rejected(self):
        result = calculations.validate_absence(
            self.employee, self.yesterday, self.week_ago, self.sick)
        self.assertIn("bad_period", [v.code for v in result.violations])

    def test_overlap_with_other_absence(self):
        self.record(self.week_ago, self.yesterday)
        result = calculations.validate_absence(
            self.employee, self.week_ago, self.yesterday, self.sick)
        violation = [v for v in result.violations if v.code == "absence_overlap"]
        self.assertTrue(violation)
        self.assertFalse(violation[0].can_be_approved)

    def test_absence_during_vacation_needs_attention(self):
        """Болезнь в отпуске — законное основание для продления."""
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=self.week_ago, date_to=self.yesterday,
            status=ScheduleEntry.Status.APPROVED)
        result = calculations.validate_absence(
            self.employee, self.week_ago, self.week_ago, self.sick)
        violation = [v for v in result.violations
                     if v.code == "absence_during_vacation"]
        self.assertTrue(violation)
        self.assertTrue(violation[0].can_be_approved)


class CountingTests(AbsenceBase):
    def test_holidays_are_not_deducted(self):
        """В отличие от отпуска, отсутствие считается календарными днями."""
        Holiday.objects.create(date=self.week_ago + timedelta(days=1),
                               name="Праздник", day_type=Holiday.DayType.HOLIDAY)
        entry = self.record(self.week_ago, self.week_ago + timedelta(days=2))
        self.assertEqual(
            calculations.absence_days(entry.date_from, entry.date_to), 3)

    def test_summary_groups_by_reason(self):
        year = self.week_ago.year
        self.record(self.week_ago, self.week_ago + timedelta(days=2))
        self.record(self.week_ago - timedelta(days=20),
                    self.week_ago - timedelta(days=20))
        self.record(self.week_ago - timedelta(days=40),
                    self.week_ago - timedelta(days=39),
                    reason=self.other, text="Вызов в военкомат")

        summary = calculations.absence_summary(self.employee, year)
        self.assertEqual(summary["episodes"], 3)
        self.assertEqual(summary["total_days"], 6)
        labels = {row["reason"]: row for row in summary["by_reason"]}
        self.assertEqual(labels["Больничный"]["days"], 4)
        self.assertEqual(labels["Больничный"]["count"], 2)
        self.assertIn("Другое: Вызов в военкомат", labels)

    def test_absence_does_not_touch_vacation_balance(self):
        before = calculations.balance_on(self.employee, date.today())
        self.record(self.week_ago, self.yesterday)
        after = calculations.balance_on(self.employee, date.today())
        self.assertEqual(before, after)

    def test_absence_does_not_block_colleagues_by_tag(self):
        """Отсутствие — факт, а не план: чужие отпуска оно не ограничивает."""
        tag = Tag.objects.create(name="Дежурная смена", max_simultaneous_absence=1)
        colleague = Employee.objects.create_user(
            email="c@a.ru", employee_number="A5", full_name="Коллега",
            hire_date=date(2020, 1, 1), org_unit=self.group)
        for person in (self.employee, colleague):
            EmployeeTag.objects.create(employee=person, tag=tag,
                                       source=Tag.Source.MANUAL)
        self.record(date(2027, 7, 5), date(2027, 7, 9), employee=colleague)

        result = calculations.validate_entry(
            self.employee, date(2027, 7, 5), date(2027, 7, 11),
            ScheduleEntry.RecordType.VACATION)
        self.assertNotIn("tag_limit_exceeded", [v.code for v in result.violations])


class AbsenceApiTests(AbsenceBase):
    def payload(self, **extra):
        data = {"date_from": self.week_ago.isoformat(),
                "date_to": self.yesterday.isoformat(),
                "reason": str(self.sick.id)}
        data.update(extra)
        return data

    def test_employee_records_own_absence(self):
        self.client.force_authenticate(self.employee)
        response = self.client.post("/api/v1/absences/", self.payload(),
                                    format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["days"], 7)
        self.assertEqual(response.json()["reason_name"], "Больничный")
        self.assertEqual(response.json()["recorded_by"], "Сотрудник")

    def test_manager_notified(self):
        self.client.force_authenticate(self.employee)
        self.client.post("/api/v1/absences/", self.payload(), format="json")
        self.assertTrue(Notification.objects.filter(recipient=self.chief).exists())

    def test_manager_records_for_subordinate(self):
        self.client.force_authenticate(self.chief)
        response = self.client.post("/api/v1/absences/",
                                    self.payload(employee=str(self.employee.id)),
                                    format="json")
        self.assertEqual(response.status_code, 201, response.content)

    def test_cannot_record_for_stranger(self):
        stranger = Employee.objects.create_user(
            email="s@a.ru", employee_number="A9", full_name="Чужой",
            hire_date=date(2020, 1, 1))
        self.client.force_authenticate(self.employee)
        response = self.client.post("/api/v1/absences/",
                                    self.payload(employee=str(stranger.id)),
                                    format="json")
        self.assertEqual(response.status_code, 403)

    def test_blocked_without_reason(self):
        self.client.force_authenticate(self.employee)
        data = self.payload()
        data.pop("reason")
        response = self.client.post("/api/v1/absences/", data, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "absence_blocked")

    def test_validate_endpoint(self):
        self.client.force_authenticate(self.employee)
        body = self.client.post("/api/v1/absences/validate/",
                                self.payload(reason=str(self.other.id)),
                                format="json").json()
        self.assertFalse(body["is_valid"])
        self.assertIn("custom_reason_required",
                      [v["code"] for v in body["violations"]])

    def test_summary_endpoint(self):
        self.record(self.week_ago, self.week_ago + timedelta(days=2))
        self.client.force_authenticate(self.employee)
        body = self.client.get(
            f"/api/v1/employees/{self.employee.id}"
            f"/absence-summary/?year={self.week_ago.year}").json()
        self.assertEqual(body["total_days"], 3)
        self.assertEqual(body["by_reason"][0]["reason"], "Больничный")

    def test_visibility(self):
        stranger = Employee.objects.create_user(
            email="s2@a.ru", employee_number="A8", full_name="Посторонний",
            hire_date=date(2020, 1, 1))
        self.record(self.week_ago, self.yesterday, employee=stranger)
        self.record(self.week_ago, self.yesterday)

        self.client.force_authenticate(self.employee)
        self.assertEqual(self.client.get("/api/v1/absences/").json()["count"], 1)
        self.client.force_authenticate(self.chief)
        self.assertEqual(self.client.get("/api/v1/absences/").json()["count"], 1)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/v1/absences/").json()["count"], 2)

    def test_author_can_delete(self):
        self.client.force_authenticate(self.employee)
        created = self.client.post("/api/v1/absences/", self.payload(),
                                   format="json").json()
        self.assertEqual(
            self.client.delete(f"/api/v1/absences/{created['id']}/").status_code, 204)

    def test_outsider_cannot_delete(self):
        self.client.force_authenticate(self.employee)
        created = self.client.post("/api/v1/absences/", self.payload(),
                                   format="json").json()
        self.client.force_authenticate(self.chief)
        self.assertEqual(
            self.client.delete(f"/api/v1/absences/{created['id']}/").status_code, 403)


class ReasonDirectoryTests(AbsenceBase):
    def test_everyone_reads(self):
        self.client.force_authenticate(self.employee)
        body = self.client.get("/api/v1/absence-reasons/").json()
        self.assertEqual(body["count"], 6)  # справочник из миграции company/0004

    def test_only_admin_edits(self):
        self.client.force_authenticate(self.employee)
        self.assertEqual(self.client.post("/api/v1/absence-reasons/", {
            "code": "my_reason", "name": "Моя причина"},
            format="json").status_code, 403)

        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post("/api/v1/absence-reasons/", {
            "code": "my_reason", "name": "Моя причина"},
            format="json").status_code, 201)
