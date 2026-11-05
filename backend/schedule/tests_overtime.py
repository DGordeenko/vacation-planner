"""Сверхурочные: ввод, два уровня согласования, отгул, пределы, отчёт."""

from datetime import date, timedelta

from company.models import CompanySettings, Holiday
from employees.models import Employee, OrgUnit
from schedule import calculations
from schedule.models import ScheduleEntry, TimeOffLedger
from schedule.tests_approvals import ApprovalBase

URL = "/api/v1/overtime/"


class OvertimeBase(ApprovalBase):
    """Структура: Иван (группа) → Пётр (отдел) → Сергей (дирекция)."""

    def setUp(self):
        super().setUp()
        self.top = OrgUnit.objects.create(name="Дирекция",
                                          unit_type=OrgUnit.UnitType.DIVISION)
        self.department.parent = self.top
        self.department.save()
        self.director = Employee.objects.create_user(
            email="dir@x.ru", employee_number="890", full_name="Сергей Директоров",
            hire_date=date(2010, 1, 1), org_unit=self.top)
        self.top.manager = self.director
        self.top.save()
        self.day = date.today() - timedelta(days=3)

    def post(self, who, **extra):
        data = {"date": self.day.isoformat(), "time_from": "18:00", "time_to": "21:30",
                "task_link": "https://jira/OPS-1", "compensation": "pay"}
        data.update(extra)
        self.client.force_authenticate(who)
        return self.client.post(URL, data, format="json")

    def act(self, who, entry_id, verb, **data):
        self.client.force_authenticate(who)
        return self.client.post(f"{URL}{entry_id}/{verb}/", data, format="json")


class OvertimeFlowTests(OvertimeBase):
    def test_employee_enters_two_levels_approve(self):
        response = self.post(self.employee)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["hours"], 3.5)
        self.assertEqual(body["status"], "pending_approval")
        self.assertEqual(body["waiting_for"], "Пётр Начальников")

        self.assertEqual(self.act(self.director, body["id"], "approve").status_code, 403)
        second = self.act(self.chief, body["id"], "approve").json()
        self.assertEqual(second["waiting_for"], "Сергей Директоров")
        self.client.force_authenticate(self.director)
        self.assertEqual(len(self.client.get("/api/v1/decisions/").json()["overtime"]), 1)
        final = self.act(self.director, body["id"], "approve").json()
        self.assertEqual(final["status"], "approved")
        self.assertIsNone(final["waiting_for"])

    def test_manager_entry_counts_as_first_approval(self):
        body = self.post(self.chief, employee=str(self.employee.id)).json()
        self.assertEqual(body["status"], "pending_approval")
        self.assertEqual(body["waiting_for"], "Сергей Директоров")
        self.assertEqual(body["created_by_name"], "Пётр Начальников")

    def test_admin_entry_is_approved(self):
        body = self.post(self.admin, employee=str(self.employee.id)).json()
        self.assertEqual(body["status"], "approved")

    def test_short_chain_without_second_manager(self):
        self.department.parent = None
        self.department.save()
        body = self.post(self.employee).json()
        final = self.act(self.chief, body["id"], "approve").json()
        self.assertEqual(final["status"], "approved")

    def test_reject_needs_comment(self):
        body = self.post(self.employee).json()
        self.assertEqual(self.act(self.chief, body["id"], "reject").status_code, 409)
        rejected = self.act(self.chief, body["id"], "reject", comment="Не согласовано").json()
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["rejection_comment"], "Не согласовано")

    def test_day_off_compensation_accrues_hours_after_final_approval(self):
        body = self.post(self.employee, compensation="day_off").json()
        self.act(self.chief, body["id"], "approve")
        self.assertFalse(TimeOffLedger.objects.exists())
        self.act(self.director, body["id"], "approve")
        row = TimeOffLedger.objects.get()
        self.assertEqual(row.reason, TimeOffLedger.Reason.OVERTIME_EARNED)
        self.assertEqual(float(calculations.time_off_balance_hours(self.employee)), 3.5)

    def test_pay_compensation_does_not_accrue(self):
        self.post(self.admin, employee=str(self.employee.id))
        self.assertFalse(TimeOffLedger.objects.exists())


class OvertimeRulesTests(OvertimeBase):
    def test_required_fields_and_period(self):
        self.assertEqual(self.post(self.employee, task_link="").status_code, 400)
        self.assertEqual(self.post(self.employee, compensation="деньги").status_code, 400)
        response = self.post(self.employee, time_from="21:00", time_to="18:00")
        self.assertEqual(response.status_code, 409)
        self.assertIn("позже начала", response.json()["detail"])

    def test_future_date_rejected(self):
        response = self.post(self.employee,
                             date=(date.today() + timedelta(days=1)).isoformat())
        self.assertEqual(response.status_code, 409)

    def test_overlap_rejected_but_rejected_entry_ignored(self):
        first = self.post(self.employee).json()
        self.assertEqual(self.post(self.employee, time_from="20:00",
                                   time_to="22:00").status_code, 409)
        self.act(self.chief, first["id"], "reject", comment="Нет")
        self.assertEqual(self.post(self.employee, time_from="20:00",
                                   time_to="22:00").status_code, 201)

    def test_only_own_structure(self):
        outsider = Employee.objects.create_user(
            email="o@x.ru", employee_number="999", full_name="Посторонний",
            hire_date=date(2017, 1, 1))
        self.assertEqual(self.post(self.chief, employee=str(outsider.id)).status_code, 403)
        self.assertEqual(self.post(self.employee, employee=str(self.chief.id)).status_code,
                         403)

    def test_limits_warn_only_when_enabled(self):
        response = self.post(self.employee, time_from="17:00", time_to="22:00")
        self.assertEqual(response.json()["warnings"], [])
        CompanySettings.objects.update(overtime_limits_enabled=True)
        response = self.post(self.employee, date=(self.day - timedelta(days=1)).isoformat(),
                             time_from="18:00", time_to="19:00")
        self.assertEqual(response.status_code, 201)  # предупреждение, не запрет
        self.assertIn("за два дня подряд", response.json()["warnings"][0])

    def test_year_limit_warning_in_preview(self):
        CompanySettings.objects.update(overtime_limits_enabled=True,
                                       overtime_limit_year_hours=3)
        self.client.force_authenticate(self.employee)
        body = self.client.post(f"{URL}validate/", {
            "date": self.day.isoformat(), "time_from": "18:00", "time_to": "21:30",
            "task_link": "https://jira/OPS-1", "compensation": "pay"}, format="json").json()
        self.assertEqual(body["hours"], 3.5)
        self.assertTrue(any("год" in w for w in body["warnings"]))

    def test_cancel_rules(self):
        body = self.post(self.employee).json()
        self.client.force_authenticate(self.chief)  # не автор и не сотрудник
        self.assertEqual(self.client.delete(f"{URL}{body['id']}/").status_code, 403)
        self.client.force_authenticate(self.employee)
        self.assertEqual(self.client.delete(f"{URL}{body['id']}/").status_code, 204)
        approved = self.post(self.admin, employee=str(self.employee.id),
                             compensation="day_off").json()
        self.client.force_authenticate(self.employee)
        self.assertEqual(self.client.delete(f"{URL}{approved['id']}/").status_code, 409)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.delete(f"{URL}{approved['id']}/").status_code, 204)
        self.assertFalse(TimeOffLedger.objects.exists())


class OvertimeVisibilityTests(OvertimeBase):
    def test_visibility(self):
        self.post(self.employee)
        self.post(self.chief)
        self.client.force_authenticate(self.employee)
        self.assertEqual(len(self.client.get(URL).json()["results"]), 1)
        self.client.force_authenticate(self.chief)
        self.assertEqual(len(self.client.get(URL).json()["results"]), 2)
        hr = Employee.objects.create_user(
            email="hr@x.ru", employee_number="777", full_name="Кадры", hire_date=date(2017, 1, 1),
            is_hr=True)
        self.client.force_authenticate(hr)
        self.assertEqual(len(self.client.get(URL).json()["results"]), 2)


class OvertimeReportTests(OvertimeBase):
    def test_report_has_only_payroll_fields(self):
        Holiday.objects.create(date=self.day, name="Праздник",
                               day_type=Holiday.DayType.HOLIDAY)
        self.post(self.admin, employee=str(self.employee.id))
        self.post(self.admin, employee=str(self.employee.id), time_from="22:00",
                  time_to="23:00", compensation="day_off")  # отгул в отчёт не идёт
        self.post(self.employee, time_from="07:00", time_to="08:00")  # не согласовано
        self.client.force_authenticate(self.admin)
        rows = self.client.get(f"{URL}report/?year={self.day.year}").json()
        self.assertEqual(rows, [{"full_name": "Иван Иванов", "employee_number": "901",
                                 "date": self.day.isoformat(), "day_type": "праздничный",
                                 "period": "18:00–21:30"}])
        csv_text = self.client.get(
            f"{URL}report/?year={self.day.year}&export=csv").content.decode("utf-8-sig")
        self.assertTrue(csv_text.startswith("ФИО;Табельный номер;Дата;Тип дня;Период работы"))
        self.assertIn("Иван Иванов;901;", csv_text)

    def test_report_closed_for_managers(self):
        self.client.force_authenticate(self.chief)
        self.assertEqual(self.client.get(f"{URL}report/?year=2026").status_code, 403)

    def test_entry_type_stays_overtime(self):
        body = self.post(self.employee).json()
        entry = ScheduleEntry.objects.get(id=body["id"])
        self.assertEqual(entry.record_type, ScheduleEntry.RecordType.OVERTIME)
