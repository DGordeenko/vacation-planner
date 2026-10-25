"""Запрос подтверждения исключения по отпуску (D5).

Отпуск с нарушением, которое может подтвердить руководитель, сохраняется
черновиком с пометкой: руководителю есть что подтверждать, а отправить
график с таким отпуском нельзя до его решения (решение от 25.10.2026).
"""

from datetime import date

from employees.models import Employee, EmployeeTag, Tag
from schedule import approvals
from schedule.models import Notification, ScheduleEntry
from schedule.tests_approvals import ApprovalBase

WEEKDAYS_ONLY = {"date_from": "2027-03-02", "date_to": "2027-03-04", "record_type": "vacation"}


class ExceptionRequestTests(ApprovalBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.employee)

    def create(self, payload=WEEKDAYS_ONLY):
        return self.client.post("/api/v1/schedule-entries/", payload, format="json")

    def test_request_saves_draft_and_notifies_manager_with_link(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        entry = ScheduleEntry.objects.get(id=response.json()["id"])
        self.assertEqual(entry.status, ScheduleEntry.Status.DRAFT)
        note = Notification.objects.get(
            recipient=self.chief,
            notification_type=Notification.NotificationType.EXCEPTION_REQUIRED)
        self.assertEqual(note.related_entity_id, entry.id)
        self.assertIn("02.03.2027—04.03.2027", note.message)

    def test_repeated_request_does_not_duplicate_unread_notification(self):
        entry = ScheduleEntry.objects.get(id=self.create().json()["id"])
        approvals.request_exception(entry, approvals.unresolved_violations(entry))
        self.assertEqual(Notification.objects.filter(
            recipient=self.chief, related_entity_id=entry.id).count(), 1)

    def test_submit_blocked_until_exception_confirmed(self):
        self.full_year_plan()
        self.create()
        with self.assertRaisesRegex(approvals.ApprovalError, "ждут подтверждения"):
            approvals.submit_year(self.employee, 2027, self.employee)

    def test_confirmed_exception_clears_mark_and_unblocks_submit(self):
        self.full_year_plan()
        entry_id = self.create().json()["id"]
        self.client.force_authenticate(self.chief)
        response = self.client.post(
            f"/api/v1/schedule-entries/{entry_id}/exception-approvals/",
            {"rule_code": "no_weekend_captured", "comment": "Согласовано с заказчиком"},
            format="json")
        self.assertEqual(response.status_code, 201)

        self.client.force_authenticate(self.employee)
        entry = self.client.get(f"/api/v1/schedule-entries/{entry_id}/").json()
        self.assertEqual(entry["pending_exceptions"], [])
        approvals.submit_year(self.employee, 2027, self.employee)

    def test_draft_awaiting_exception_does_not_take_colleague_slot(self):
        tag = Tag.objects.create(name="Дежурная смена", max_simultaneous_absence=1)
        colleague = Employee.objects.create_user(
            email="col@x.ru", employee_number="903", full_name="Анна Коллегова",
            hire_date=date(2018, 1, 1), org_unit=self.group)
        for person in (self.employee, colleague):
            EmployeeTag.objects.create(employee=person, tag=tag, source=Tag.Source.MANUAL)
        self.create()
        self.client.force_authenticate(colleague)
        body = self.client.post("/api/v1/schedule-entries/validate/", WEEKDAYS_ONLY,
                                format="json").json()
        self.assertNotIn("tag_limit_exceeded", [v["code"] for v in body["violations"]])


class TagConflictDetailsTests(ApprovalBase):
    """При превышении лимита видно, кто из коллег в отпуске и когда."""

    def test_colleagues_listed_with_dates(self):
        tag = Tag.objects.create(name="Дежурная смена", max_simultaneous_absence=1)
        colleague = Employee.objects.create_user(
            email="col@x.ru", employee_number="903", full_name="Анна Коллегова",
            hire_date=date(2018, 1, 1), org_unit=self.group)
        for person in (self.employee, colleague):
            EmployeeTag.objects.create(employee=person, tag=tag, source=Tag.Source.MANUAL)
        ScheduleEntry.objects.create(
            employee=colleague, created_by=colleague,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 5), date_to=date(2027, 7, 18),
            status=ScheduleEntry.Status.APPROVED)

        self.client.force_authenticate(self.employee)
        body = self.client.post("/api/v1/schedule-entries/validate/", {
            "date_from": "2027-07-12", "date_to": "2027-07-25", "record_type": "vacation",
        }, format="json").json()
        violation = next(v for v in body["violations"] if v["code"] == "tag_limit_exceeded")
        self.assertEqual(violation["colleagues"], [
            {"name": "Анна Коллегова", "date_from": "2027-07-05", "date_to": "2027-07-18"}])
        self.assertIn("12.07.2027—25.07.2027", violation["message"])
