"""«Требует моего решения» и календарь команды."""

from datetime import date

from employees.models import Employee, EmployeeTag, Tag
from schedule import approvals
from schedule.models import ScheduleApproval, ScheduleEntry
from schedule.tests_approvals import ApprovalBase


class DecisionsTests(ApprovalBase):
    def test_submitted_plan_awaits_manager(self):
        self.full_year_plan()
        approvals.submit_year(self.employee, 2027, self.employee)
        self.client.force_authenticate(self.chief)
        body = self.client.get("/api/v1/decisions/").json()
        self.assertEqual(body["count"], 1)
        item = body["approvals"][0]
        self.assertEqual((item["employee_name"], item["planned_days"]), ("Иван Иванов", 28))
        self.assertEqual(len(item["entries"]), 2)

    def test_next_level_sees_plan_only_after_previous_decision(self):
        CompanySettings = __import__("company.models", fromlist=["x"]).CompanySettings
        CompanySettings.objects.update(approval_levels=4)
        group_lead = Employee.objects.create_user(
            email="lead@x.ru", employee_number="910", full_name="Руководитель группы",
            hire_date=date(2016, 1, 1), org_unit=self.group)
        self.group.manager = group_lead
        self.group.save()
        self.full_year_plan()
        approval = approvals.submit_year(self.employee, 2027, self.employee)
        self.client.force_authenticate(self.chief)
        self.assertEqual(self.client.get("/api/v1/decisions/").json()["count"], 0)

        step = approval.steps.get(order=1)
        approvals.decide_step(step, group_lead, True)
        self.assertEqual(self.client.get("/api/v1/decisions/").json()["count"], 1)

    def test_approve_from_list_closes_item(self):
        self.full_year_plan()
        approvals.submit_year(self.employee, 2027, self.employee)
        self.client.force_authenticate(self.chief)
        item = self.client.get("/api/v1/decisions/").json()["approvals"][0]
        response = self.client.post(
            f"/api/v1/schedule-approvals/{item['approval_id']}/steps/{item['step_id']}/approve/",
            {"comment": ""}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/decisions/").json()["count"], 0)
        self.assertEqual(ScheduleApproval.objects.get().status, ScheduleApproval.Status.APPROVED)

    def test_exception_request_listed_and_rejected(self):
        self.client.force_authenticate(self.employee)
        entry_id = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04", "record_type": "vacation",
        }, format="json").json()["id"]

        self.client.force_authenticate(self.chief)
        exceptions = self.client.get("/api/v1/decisions/").json()["exceptions"]
        self.assertEqual([e["id"] for e in exceptions], [entry_id])
        self.assertEqual(exceptions[0]["violations"][0]["code"], "no_weekend_captured")

        no_comment = self.client.post(
            f"/api/v1/schedule-entries/{entry_id}/exception-rejection/", {}, format="json")
        self.assertEqual(no_comment.status_code, 409)
        response = self.client.post(
            f"/api/v1/schedule-entries/{entry_id}/exception-rejection/",
            {"comment": "В эти даты релиз"}, format="json")
        self.assertEqual(response.status_code, 200)
        entry = ScheduleEntry.objects.get(id=entry_id)
        self.assertEqual(entry.status, ScheduleEntry.Status.REJECTED)
        self.assertEqual(entry.rejection_comment, "В эти даты релиз")
        # Сотрудник видит отказ и причину у самого отпуска и в ленте.
        self.client.force_authenticate(self.employee)
        mine = self.client.get(f"/api/v1/schedule-entries/{entry_id}/").json()
        self.assertEqual((mine["status"], mine["rejection_comment"]),
                         ("rejected", "В эти даты релиз"))
        note = self.client.get("/api/v1/notifications/").json()["results"][0]
        self.assertIn("не подтвердил отпуск 02.03.2027—04.03.2027: В эти даты релиз",
                      note["message"])
        # Отклонённый отпуск сотрудник может убрать из графика.
        response = self.client.delete(f"/api/v1/schedule-entries/{entry_id}/")
        self.assertEqual(response.status_code, 204)
        self.client.force_authenticate(self.chief)
        self.assertEqual(self.client.get("/api/v1/decisions/").json()["count"], 0)

    def test_stranger_cannot_reject(self):
        self.client.force_authenticate(self.employee)
        entry_id = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04", "record_type": "vacation",
        }, format="json").json()["id"]
        response = self.client.post(
            f"/api/v1/schedule-entries/{entry_id}/exception-rejection/",
            {"comment": "Нет"}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_confirmed_exception_leaves_list(self):
        self.client.force_authenticate(self.employee)
        entry_id = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04", "record_type": "vacation",
        }, format="json").json()["id"]
        self.client.force_authenticate(self.chief)
        self.client.post(f"/api/v1/schedule-entries/{entry_id}/exception-approvals/",
                         {"rule_code": "no_weekend_captured", "comment": "Ок"}, format="json")
        self.assertEqual(self.client.get("/api/v1/decisions/").json()["count"], 0)


class TeamCalendarTests(ApprovalBase):
    def setUp(self):
        super().setUp()
        self.tag = Tag.objects.create(name="Дежурство", max_simultaneous_absence=1)
        self.colleague = Employee.objects.create_user(
            email="col@x.ru", employee_number="905", full_name="Анна Коллегова",
            hire_date=date(2018, 1, 1), org_unit=self.group)
        for person in (self.employee, self.colleague):
            EmployeeTag.objects.create(employee=person, tag=self.tag, source=Tag.Source.MANUAL)

    def get(self, who, **params):
        self.client.force_authenticate(who)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"/api/v1/team-calendar/?{query}")

    def test_employee_has_no_access(self):
        self.assertEqual(self.get(self.employee).status_code, 403)

    def test_rows_and_entries_for_period(self):
        self.add_vacation(date(2027, 7, 5), date(2027, 7, 18),
                          status=ScheduleEntry.Status.APPROVED)
        body = self.get(self.chief, date_from="2027-07-01", date_to="2027-07-31").json()
        rows = {r["full_name"]: r for r in body["rows"]}
        self.assertIn("Анна Коллегова", rows)
        self.assertEqual(rows["Иван Иванов"]["entries"][0]["date_from"], "2027-07-05")

    def test_limit_reached_days_by_tag(self):
        self.add_vacation(date(2027, 7, 5), date(2027, 7, 7),
                          status=ScheduleEntry.Status.APPROVED)
        ScheduleEntry.objects.create(  # черновик места не занимает
            employee=self.colleague, created_by=self.colleague,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 20), date_to=date(2027, 7, 21))
        body = self.get(self.chief, date_from="2027-07-01", date_to="2027-07-31",
                        tag_id=self.tag.id).json()
        self.assertEqual([d["date"] for d in body["limit_reached"]],
                         ["2027-07-05", "2027-07-06", "2027-07-07"])
        self.assertEqual(body["tag"]["limit"], 1)

    def test_period_longer_than_quarter_rejected(self):
        response = self.get(self.chief, date_from="2027-01-01", date_to="2027-12-31")
        self.assertEqual(response.status_code, 400)


class TeamCalendarScopeTests(ApprovalBase):
    """Своя ветка по умолчанию, вся компания — по выбору руководителя."""

    def setUp(self):
        super().setUp()
        from employees.models import OrgUnit
        other = OrgUnit.objects.create(name="Отдел Чужой",
                                       unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.other_chief = Employee.objects.create_user(
            email="oc@x.ru", employee_number="910", full_name="Олег Соседов",
            hire_date=date(2015, 1, 1), org_unit=other)
        other.manager = self.other_chief
        other.save()
        self.stranger = Employee.objects.create_user(
            email="st@x.ru", employee_number="911", full_name="Сергей Чужой",
            hire_date=date(2018, 1, 1), org_unit=other)
        self.stranger_draft = self.vacation(self.stranger, 6, 1, 7)
        self.stranger_approved = self.vacation(self.stranger, 7, 12, 18,
                                               ScheduleEntry.Status.APPROVED)

    def vacation(self, who, month, first, last, status=ScheduleEntry.Status.DRAFT):
        return ScheduleEntry.objects.create(
            employee=who, created_by=who, record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, month, first), date_to=date(2027, month, last), status=status)

    def calendar(self, who, month, **params):
        self.client.force_authenticate(who)
        query = "".join(f"&{k}={v}" for k, v in params.items())
        return self.client.get(f"/api/v1/team-calendar/?date_from=2027-{month:02d}-01"
                               f"&date_to=2027-{month:02d}-28{query}").json()

    def test_own_branch_by_default(self):
        body = self.calendar(self.chief, 7)
        names = {r["full_name"] for r in body["rows"]}
        self.assertNotIn("Сергей Чужой", names)
        self.assertIn("Иван Иванов", names)
        self.assertEqual(body["scope"], "mine")
        self.assertTrue(body["can_view_all"])

    def test_all_departments_show_only_planned_entries_of_others(self):
        self.add_vacation(date(2027, 6, 14), date(2027, 6, 18))  # свой черновик виден
        body = self.calendar(self.chief, 6, scope="all")
        rows = {r["full_name"]: r for r in body["rows"]}
        self.assertEqual(body["scope"], "all")
        self.assertFalse(rows["Сергей Чужой"]["own"])
        self.assertEqual(rows["Сергей Чужой"]["entries"], [])  # чужой черновик скрыт
        self.assertEqual(len(rows["Иван Иванов"]["entries"]), 1)
        self.assertTrue(rows["Иван Иванов"]["own"])
        july = {r["full_name"]: r for r in self.calendar(self.chief, 7, scope="all")["rows"]}
        self.assertEqual(july["Сергей Чужой"]["entries"][0]["status"], "approved")

    def test_admin_always_sees_everyone(self):
        body = self.calendar(self.admin, 7)
        self.assertIn("Сергей Чужой", {r["full_name"] for r in body["rows"]})
        self.assertFalse(body["can_view_all"])

    def test_pending_plan_shows_current_approver(self):
        self.full_year_plan()
        approvals.submit_year(self.employee, 2027, self.employee)
        rows = {r["full_name"]: r for r in self.calendar(self.chief, 7)["rows"]}
        entry = rows["Иван Иванов"]["entries"][0]
        self.assertEqual(entry["status"], "pending_approval")
        self.assertEqual(entry["waiting_for"], "Пётр Начальников")

        detail = self.client.get(f"/api/v1/team-calendar/entries/{entry['id']}/").json()
        self.assertEqual(detail["employee_name"], "Иван Иванов")
        self.assertEqual([s["approver_name"] for s in detail["steps"] if s["is_current"]],
                         ["Пётр Начальников"])
        self.assertIsNone(detail["exception"])

    def test_draft_with_exception_request_shows_approver(self):
        self.client.force_authenticate(self.employee)
        entry_id = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04", "record_type": "vacation",
        }, format="json").json()["id"]
        rows = {r["full_name"]: r for r in self.calendar(self.chief, 3)["rows"]}
        self.assertEqual(rows["Иван Иванов"]["entries"][0]["waiting_for"], "Пётр Начальников")
        detail = self.client.get(f"/api/v1/team-calendar/entries/{entry_id}/").json()
        self.assertEqual(detail["exception"]["approver_name"], "Пётр Начальников")
        self.assertTrue(detail["exception"]["violations"])

    def test_detail_respects_visibility(self):
        self.client.force_authenticate(self.chief)
        url = "/api/v1/team-calendar/entries/{}/"
        self.assertEqual(self.client.get(url.format(self.stranger_draft.id)).status_code, 404)
        self.assertEqual(self.client.get(url.format(self.stranger_approved.id)).status_code,
                         200)
        self.client.force_authenticate(self.employee)
        self.assertEqual(self.client.get(url.format(self.stranger_approved.id)).status_code,
                         403)
