"""HR-администратор: видит всех, ничего не меняет."""

from datetime import date, timedelta

from django.test import TestCase

from company.models import AbsenceReason, CompanySettings
from employees.models import Employee, EmployeeTag, OrgUnit, Tag
from schedule.models import ScheduleApproval, ScheduleEntry
from schedule.tests_approvals import ApprovalBase


class HrViewerTests(ApprovalBase):
    def setUp(self):
        super().setUp()
        other = OrgUnit.objects.create(name="Бухгалтерия",
                                       unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.hr = Employee.objects.create_user(
            email="hr@x.ru", employee_number="950", full_name="Ирина Кадрова",
            hire_date=date(2017, 1, 1), org_unit=other)
        self.hr.is_hr = True
        self.hr.save()
        self.draft = self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))

    def as_hr(self):
        self.client.force_authenticate(self.hr)
        return self.client

    def test_profile_flag(self):
        body = self.as_hr().get("/api/v1/auth/me/").json()
        self.assertTrue(body["is_hr"])
        self.assertFalse(body["is_admin"])

    def test_sees_everyone_in_team_calendar_with_drafts(self):
        body = self.as_hr().get(
            "/api/v1/team-calendar/?date_from=2027-07-01&date_to=2027-07-31").json()
        rows = {r["full_name"]: r for r in body["rows"]}
        self.assertIn("Пётр Начальников", rows)
        self.assertEqual(rows["Иван Иванов"]["entries"][0]["status"], "draft")
        self.assertFalse(body["can_view_all"])  # как у администратора: всё сразу

    def test_reads_employee_data_and_full_audit(self):
        client = self.as_hr()
        url = f"/api/v1/employees/{self.employee.id}/annual-summary/?year=2027"
        self.assertEqual(client.get(url).status_code, 200)
        CompanySettings.objects.update(approval_levels=3)
        settings_row = CompanySettings.get_solo()
        settings_row.approval_levels = 2
        settings_row.save()  # запись в журнале без сотрудника-субъекта
        types = {r["entity_type"] for r in client.get("/api/v1/audit-log/").json()["results"]}
        self.assertIn("CompanySettings", types)

    def test_cannot_change_others(self):
        client = self.as_hr()
        self.assertEqual(client.delete(f"/api/v1/schedule-entries/{self.draft.id}/").status_code,
                         403)
        self.assertEqual(client.patch("/api/v1/settings/", {"approval_levels": 3},
                                      format="json").status_code, 403)
        tag = Tag.objects.create(name="Касса")
        self.assertEqual(client.post(f"/api/v1/tags/{tag.id}/assign/",
                                     {"employee_id": str(self.employee.id)},
                                     format="json").status_code, 403)
        self.assertFalse(EmployeeTag.objects.exists())
        self.assertEqual(client.post("/api/v1/admin/rvd/", {
            "employee": str(self.employee.id), "date_from": "2026-10-03",
            "task_link": "https://t/1"}, format="json").status_code, 403)
        self.assertEqual(client.post("/api/v1/settings/hr-viewers/",
                                     {"employee_id": str(self.employee.id), "value": True},
                                     format="json").status_code, 403)

    def test_cannot_record_absence_for_others(self):
        reason = AbsenceReason.objects.create(name="Больничный")
        yesterday = date.today() - timedelta(days=1)
        response = self.as_hr().post("/api/v1/absences/", {
            "employee": str(self.employee.id), "date_from": yesterday.isoformat(),
            "date_to": yesterday.isoformat(), "reason": str(reason.id)}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_hr_manager_keeps_only_own_structure_rights(self):
        self.department.manager = self.hr
        self.department.save()
        reason = AbsenceReason.objects.create(name="Больничный")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        outsider = Employee.objects.create_user(
            email="o@x.ru", employee_number="951", full_name="Посторонний",
            hire_date=date(2017, 1, 1))
        client = self.as_hr()
        ok = client.post("/api/v1/absences/", {"employee": str(self.employee.id),
                                                "date_from": yesterday, "date_to": yesterday,
                                                "reason": str(reason.id)}, format="json")
        self.assertEqual(ok.status_code, 201, ok.content)
        denied = client.post("/api/v1/absences/", {"employee": str(outsider.id),
                                                    "date_from": yesterday, "date_to": yesterday,
                                                    "reason": str(reason.id)}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_not_an_approver(self):
        from schedule import approvals
        self.draft.delete()
        self.full_year_plan()
        approval = approvals.submit_year(self.employee, 2027, self.employee)
        step = approval.steps.order_by("level").first()
        response = self.as_hr().post(
            f"/api/v1/schedule-approvals/{approval.id}/steps/{step.id}/approve/",
            {}, format="json")
        self.assertIn(response.status_code, (403, 404, 409))
        step.refresh_from_db()
        self.assertEqual(step.status, ScheduleApproval.Status.PENDING)
        self.assertEqual(self.as_hr().get("/api/v1/decisions/").json()["count"], 0)

    def test_own_vacation_still_editable(self):
        response = self.as_hr().post("/api/v1/schedule-entries/", {
            "record_type": ScheduleEntry.RecordType.VACATION,
            "date_from": "2027-08-02", "date_to": "2027-08-15"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)


class HrAssignmentTests(TestCase):
    def test_admin_assigns_and_removes(self):
        from rest_framework.test import APIClient
        admin = Employee.objects.create_user(
            email="a@h.ru", employee_number="1", full_name="Админ", hire_date=date(2015, 1, 1),
            is_admin=True)
        person = Employee.objects.create_user(
            email="p@h.ru", employee_number="2", full_name="Ирина", hire_date=date(2015, 1, 1))
        client = APIClient()
        client.force_authenticate(admin)
        url = "/api/v1/settings/hr-viewers/"
        body = client.post(url, {"employee_id": str(person.id), "value": True},
                           format="json").json()
        self.assertEqual([r["full_name"] for r in body], ["Ирина"])
        self.assertEqual(client.post(url, {"employee_id": str(person.id), "value": "да"},
                                     format="json").status_code, 400)
        body = client.post(url, {"employee_id": str(person.id), "value": False},
                           format="json").json()
        self.assertEqual(body, [])
