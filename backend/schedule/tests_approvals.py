"""Тесты согласования графика и подтверждения исключений (группы D5, F1-F3)."""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from company.models import CompanySettings
from employees.models import Employee, OrgUnit
from schedule import approvals
from schedule.models import (
    ApprovalStep,
    ExceptionApproval,
    Notification,
    ScheduleApproval,
    ScheduleEntry,
)
from schedule.tests import seed_rules


class ApprovalBase(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()

        self.department = OrgUnit.objects.create(
            name="Отдел Т", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.group = OrgUnit.objects.create(
            name="Группа Т", unit_type=OrgUnit.UnitType.GROUP, parent=self.department)

        self.chief = Employee.objects.create_user(
            email="chief@x.ru", employee_number="900", full_name="Пётр Начальников",
            hire_date=date(2015, 1, 1), org_unit=self.department, password="pw12345")
        self.department.manager = self.chief
        self.department.save()

        self.employee = Employee.objects.create_user(
            email="emp@x.ru", employee_number="901", full_name="Иван Иванов",
            hire_date=date(2018, 1, 1), org_unit=self.group, password="pw12345")

        self.admin = Employee.objects.create_user(
            email="admin@x.ru", employee_number="902", full_name="Админ Админов",
            hire_date=date(2016, 1, 1), org_unit=self.department, password="pw12345")
        self.admin.is_admin = True
        self.admin.save()

    def add_vacation(self, start, end, status=ScheduleEntry.Status.DRAFT):
        return ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=start, date_to=end, status=status)

    def full_year_plan(self):
        """Корректный график: 28 дней, из них непрерывный отпуск на 14."""
        self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))   # 14 дней
        self.add_vacation(date(2027, 9, 6), date(2027, 9, 19))   # 14 дней


class ChainTests(ApprovalBase):
    def test_chain_is_direct_manager(self):
        chain = approvals.build_chain(self.employee, levels=1)
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0][0], self.chief)
        self.assertEqual(chain[0][1], ApprovalStep.Level.DEPARTMENT)

    def test_chain_falls_back_to_admin_for_top_manager(self):
        """У руководителя без вышестоящего согласующим становится администратор."""
        chain = approvals.build_chain(self.chief, levels=1)
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0][0], self.admin)
        self.assertEqual(chain[0][1], ApprovalStep.Level.ADMIN)


class SubmitTests(ApprovalBase):
    def test_f1_submit_and_approve(self):
        self.full_year_plan()
        approval = approvals.submit_year(self.employee, 2027, self.employee)
        self.assertEqual(approval.status, ScheduleApproval.Status.PENDING)
        self.assertEqual(approval.steps.count(), 1)
        self.assertTrue(ScheduleEntry.objects.filter(
            employee=self.employee,
            status=ScheduleEntry.Status.PENDING_APPROVAL).exists())

        step = approval.steps.first()
        approvals.decide_step(step, self.chief, approved=True)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ScheduleApproval.Status.APPROVED)
        self.assertEqual(
            ScheduleEntry.objects.filter(
                employee=self.employee,
                status=ScheduleEntry.Status.APPROVED).count(), 2)

    def test_submit_blocked_when_norm_not_met(self):
        self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))  # только 14 дней
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.submit_year(self.employee, 2027, self.employee)
        self.assertIn("14", str(ctx.exception))
        self.assertFalse(ScheduleApproval.objects.exists())

    def test_submit_blocked_notifies_manager(self):
        self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))
        with self.assertRaises(approvals.ApprovalError):
            approvals.submit_year(self.employee, 2027, self.employee)
        self.assertTrue(Notification.objects.filter(
            recipient=self.chief,
            notification_type=Notification.NotificationType.EXCEPTION_REQUIRED).exists())

    def test_empty_schedule_cannot_be_submitted(self):
        with self.assertRaises(approvals.ApprovalError):
            approvals.submit_year(self.employee, 2027, self.employee)

    def test_double_submit_rejected(self):
        self.full_year_plan()
        approvals.submit_year(self.employee, 2027, self.employee)
        with self.assertRaises(approvals.ApprovalError):
            approvals.submit_year(self.employee, 2027, self.employee)


class DecisionTests(ApprovalBase):
    def _submit(self):
        self.full_year_plan()
        return approvals.submit_year(self.employee, 2027, self.employee)

    def test_f2_reject_requires_comment(self):
        approval = self._submit()
        step = approval.steps.first()
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide_step(step, self.chief, approved=False, comment="")

    def test_reject_returns_entries_to_draft(self):
        approval = self._submit()
        approvals.decide_step(approval.steps.first(), self.chief,
                              approved=False, comment="Смести июль")
        approval.refresh_from_db()
        self.assertEqual(approval.status, ScheduleApproval.Status.REJECTED)
        self.assertEqual(
            ScheduleEntry.objects.filter(
                employee=self.employee, status=ScheduleEntry.Status.DRAFT).count(), 2)

    def test_stranger_cannot_decide(self):
        approval = self._submit()
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide_step(approval.steps.first(), self.employee, approved=True)

    def test_admin_can_decide(self):
        approval = self._submit()
        approvals.decide_step(approval.steps.first(), self.admin, approved=True)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ScheduleApproval.Status.APPROVED)

    def test_decision_twice_rejected(self):
        approval = self._submit()
        step = approval.steps.first()
        approvals.decide_step(step, self.chief, approved=True)
        step.refresh_from_db()
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide_step(step, self.chief, approved=True)

    def test_employee_notified_about_decision(self):
        approval = self._submit()
        approvals.decide_step(approval.steps.first(), self.chief, approved=True)
        self.assertTrue(Notification.objects.filter(
            recipient=self.employee,
            notification_type=Notification.NotificationType.APPROVAL_DECIDED).exists())


class WithdrawTests(ApprovalBase):
    def test_withdraw_returns_to_draft(self):
        self.full_year_plan()
        approval = approvals.submit_year(self.employee, 2027, self.employee)
        approvals.withdraw(approval, self.employee)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ScheduleApproval.Status.WITHDRAWN)
        self.assertEqual(
            ScheduleEntry.objects.filter(
                employee=self.employee, status=ScheduleEntry.Status.DRAFT).count(), 2)

    def test_cannot_withdraw_foreign_schedule(self):
        self.full_year_plan()
        approval = approvals.submit_year(self.employee, 2027, self.employee)
        with self.assertRaises(approvals.ApprovalError):
            approvals.withdraw(approval, self.chief)

    def test_cannot_withdraw_after_decision(self):
        self.full_year_plan()
        approval = approvals.submit_year(self.employee, 2027, self.employee)
        approvals.decide_step(approval.steps.first(), self.chief, approved=True)
        approval.refresh_from_db()
        with self.assertRaises(approvals.ApprovalError):
            approvals.withdraw(approval, self.employee)


class ExceptionTests(ApprovalBase):
    def test_d5_comment_is_mandatory(self):
        entry = self.add_vacation(date(2027, 3, 2), date(2027, 3, 4))
        with self.assertRaises(approvals.ApprovalError):
            approvals.approve_exception(entry, "no_weekend_captured", self.chief, "")

    def test_exception_recorded_with_author(self):
        entry = self.add_vacation(date(2027, 3, 2), date(2027, 3, 4))
        approval = approvals.approve_exception(
            entry, "no_weekend_captured", self.chief, "Согласовано устно")
        self.assertEqual(approval.approved_by, self.chief)
        self.assertEqual(ExceptionApproval.objects.count(), 1)

    def test_exception_clears_violation(self):
        entry = self.add_vacation(date(2027, 3, 2), date(2027, 3, 4))
        self.assertTrue(any(v.code == "no_weekend_captured"
                            for v in approvals.unresolved_violations(entry)))
        approvals.approve_exception(entry, "no_weekend_captured", self.chief, "ок")
        self.assertFalse(any(v.code == "no_weekend_captured"
                             for v in approvals.unresolved_violations(entry)))

    def test_stranger_cannot_approve_exception(self):
        entry = self.add_vacation(date(2027, 3, 2), date(2027, 3, 4))
        other = Employee.objects.create_user(
            email="other@x.ru", employee_number="903", full_name="Чужой",
            hire_date=date(2020, 1, 1))
        with self.assertRaises(approvals.ApprovalError):
            approvals.approve_exception(entry, "no_weekend_captured", other, "ок")

    def test_employee_notified_about_exception(self):
        entry = self.add_vacation(date(2027, 3, 2), date(2027, 3, 4))
        approvals.approve_exception(entry, "no_weekend_captured", self.chief, "ок")
        self.assertTrue(Notification.objects.filter(recipient=self.employee).exists())


class ApprovalApiTests(ApprovalBase):
    def test_full_cycle_through_api(self):
        self.full_year_plan()
        self.client.force_authenticate(self.employee)
        response = self.client.post(
            f"/api/v1/employees/{self.employee.id}/submit-year/?year=2027")
        self.assertEqual(response.status_code, 201, response.content)
        approval_id = response.json()["id"]
        step_id = response.json()["steps"][0]["id"]

        self.client.force_authenticate(self.chief)
        inbox = self.client.get("/api/v1/notifications/")
        self.assertGreaterEqual(inbox.json()["count"], 1)

        decision = self.client.post(
            f"/api/v1/schedule-approvals/{approval_id}/steps/{step_id}/approve/",
            {"comment": "Утверждаю"}, format="json")
        self.assertEqual(decision.status_code, 200, decision.content)
        self.assertEqual(decision.json()["status"], "approved")

    def test_submit_blocked_returns_409(self):
        self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))
        self.client.force_authenticate(self.employee)
        response = self.client.post(
            f"/api/v1/employees/{self.employee.id}/submit-year/?year=2027")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "submit_blocked")

    def test_exception_through_api(self):
        entry = self.add_vacation(date(2027, 3, 2), date(2027, 3, 4))
        self.client.force_authenticate(self.chief)
        response = self.client.post(
            f"/api/v1/schedule-entries/{entry.id}/exception-approvals/",
            {"rule_code": "no_weekend_captured", "comment": "Разрешаю"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["remaining_violations"], [])

    def test_exception_without_comment_returns_409(self):
        entry = self.add_vacation(date(2027, 3, 2), date(2027, 3, 4))
        self.client.force_authenticate(self.chief)
        response = self.client.post(
            f"/api/v1/schedule-entries/{entry.id}/exception-approvals/",
            {"rule_code": "no_weekend_captured", "comment": ""}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_manager_sees_pending_approvals(self):
        self.full_year_plan()
        approvals.submit_year(self.employee, 2027, self.employee)
        self.client.force_authenticate(self.chief)
        response = self.client.get("/api/v1/schedule-approvals/?status=pending")
        self.assertEqual(response.json()["count"], 1)

    def test_employee_does_not_see_foreign_approvals(self):
        self.full_year_plan()
        approvals.submit_year(self.employee, 2027, self.employee)
        stranger = Employee.objects.create_user(
            email="s@x.ru", employee_number="904", full_name="Посторонний",
            hire_date=date(2020, 1, 1))
        self.client.force_authenticate(stranger)
        response = self.client.get("/api/v1/schedule-approvals/")
        self.assertEqual(response.json()["count"], 0)
