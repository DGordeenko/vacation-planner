"""Актуализация утверждённого графика."""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from schedule import approvals
from schedule.models import ApprovalStep, Notification, ScheduleApproval, ScheduleEntry
from schedule.tests_multilevel import FullHierarchyBase


class AmendmentBase(FullHierarchyBase):
    def approved_year(self, employee=None):
        """Проводит график через все четыре уровня до утверждения."""
        target = employee or self.employee
        self.plan(target)
        approval = approvals.submit_year(target, 2027, target)
        for step in approval.steps.all():
            approvals.decide_step(step, step.approver, approved=True)
        approval.refresh_from_db()
        assert approval.status == ScheduleApproval.Status.APPROVED
        return approval

    def entries(self, employee=None):
        return ScheduleEntry.objects.filter(
            employee=employee or self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
        ).exclude(status=ScheduleEntry.Status.CANCELLED).order_by("date_from")

    def periods(self, employee=None):
        return [(e.date_from.isoformat(), e.effective_date_to.isoformat())
                for e in self.entries(employee)]


class OpeningTests(AmendmentBase):
    def test_cannot_open_before_approval(self):
        self.plan()
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.open_amendment(self.employee, 2027, self.employee)
        self.assertIn("ещё не утверждён", str(ctx.exception))

    def test_open_unlocks_entries(self):
        self.approved_year()
        amendment = approvals.open_amendment(self.employee, 2027, self.employee)

        self.assertEqual(amendment.scope, ScheduleApproval.Scope.AMENDMENT)
        self.assertEqual(amendment.status, ScheduleApproval.Status.OPEN)
        self.assertEqual(len(amendment.snapshot), 2)
        self.assertTrue(all(e.status == ScheduleEntry.Status.DRAFT
                            for e in self.entries()))

    def test_only_owner_can_open(self):
        self.approved_year()
        with self.assertRaises(approvals.ApprovalError):
            approvals.open_amendment(self.employee, 2027, self.dept_head)

    def test_double_open_rejected(self):
        self.approved_year()
        approvals.open_amendment(self.employee, 2027, self.employee)
        with self.assertRaises(approvals.ApprovalError):
            approvals.open_amendment(self.employee, 2027, self.employee)


class AmendmentChainTests(AmendmentBase):
    def test_two_levels_above_employee(self):
        chain = approvals.build_chain_for_amendment(self.employee)
        self.assertEqual([person.employee_number for person, _ in chain],
                         ["G1", "O1"])

    def test_for_manager_starts_above_him(self):
        """Правку графика руководителя согласуют те, кто выше него."""
        chain = approvals.build_chain_for_amendment(self.dept_head)
        self.assertEqual([person.employee_number for person, _ in chain],
                         ["P1", "D1"])

    def test_admin_completes_short_chain(self):
        """Для вершины структуры вторую подпись ставит администратор."""
        chain = approvals.build_chain_for_amendment(self.unit_head)
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0][0], self.division_head)
        self.assertEqual(chain[1][0], self.admin)
        self.assertEqual(chain[1][1], ApprovalStep.Level.ADMIN)


class SubmitAmendmentTests(AmendmentBase):
    def setUp(self):
        super().setUp()
        self.approved_year()
        self.amendment = approvals.open_amendment(self.employee, 2027, self.employee)

    def _replace_period(self):
        self.entries().filter(date_from=date(2027, 9, 6)).delete()
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 10, 4), date_to=date(2027, 10, 17))

    def test_unchanged_schedule_cannot_be_submitted(self):
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.submit_amendment(self.amendment, self.employee)
        self.assertIn("не изменился", str(ctx.exception))

    def test_submit_creates_two_steps(self):
        self._replace_period()
        approvals.submit_amendment(self.amendment, self.employee)
        self.amendment.refresh_from_db()
        self.assertEqual(self.amendment.status, ScheduleApproval.Status.PENDING)
        self.assertEqual(
            [step.approver.employee_number for step in self.amendment.steps.all()],
            ["G1", "O1"])

    def test_only_first_approver_notified(self):
        self._replace_period()
        Notification.objects.all().delete()
        approvals.submit_amendment(self.amendment, self.employee)
        recipients = set(Notification.objects.values_list(
            "recipient__employee_number", flat=True))
        self.assertEqual(recipients, {"G1"})

    def test_amendment_must_pass_annual_rules(self):
        """Правка не может оставить график ниже годовой нормы."""
        self.entries().filter(date_from=date(2027, 9, 6)).delete()
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.submit_amendment(self.amendment, self.employee)
        self.assertIn("годовые проверки", str(ctx.exception))


class RestoreTests(AmendmentBase):
    """Отказ и отмена возвращают ровно утверждённый график."""

    def setUp(self):
        super().setUp()
        self.approved_year()
        self.original = self.periods()
        self.amendment = approvals.open_amendment(self.employee, 2027, self.employee)
        self.entries().filter(date_from=date(2027, 9, 6)).delete()
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 10, 4), date_to=date(2027, 10, 17))

    def test_cancel_restores_original(self):
        approvals.cancel_amendment(self.amendment, self.employee)
        self.assertEqual(self.periods(), self.original)
        self.assertTrue(all(e.status == ScheduleEntry.Status.APPROVED
                            for e in self.entries()))

    def test_rejection_restores_original(self):
        approvals.submit_amendment(self.amendment, self.employee)
        self.amendment.refresh_from_db()
        first = self.amendment.steps.get(order=1)
        approvals.decide_step(first, self.group_head, approved=False,
                              comment="Октябрь не подходит")

        self.amendment.refresh_from_db()
        self.assertEqual(self.amendment.status, ScheduleApproval.Status.REJECTED)
        self.assertEqual(self.periods(), self.original)
        self.assertTrue(all(e.status == ScheduleEntry.Status.APPROVED
                            for e in self.entries()))

    def test_rejection_on_second_level_also_restores(self):
        approvals.submit_amendment(self.amendment, self.employee)
        self.amendment.refresh_from_db()
        approvals.decide_step(self.amendment.steps.get(order=1),
                              self.group_head, approved=True)
        approvals.decide_step(self.amendment.steps.get(order=2),
                              self.dept_head, approved=False, comment="Нет")
        self.assertEqual(self.periods(), self.original)

    def test_withdraw_restores_original(self):
        approvals.submit_amendment(self.amendment, self.employee)
        self.amendment.refresh_from_db()
        approvals.withdraw(self.amendment, self.employee)
        self.assertEqual(self.periods(), self.original)

    def test_approval_keeps_changes(self):
        changed = self.periods()
        approvals.submit_amendment(self.amendment, self.employee)
        self.amendment.refresh_from_db()
        approvals.decide_step(self.amendment.steps.get(order=1),
                              self.group_head, approved=True)
        approvals.decide_step(self.amendment.steps.get(order=2),
                              self.dept_head, approved=True)

        self.amendment.refresh_from_db()
        self.assertEqual(self.amendment.status, ScheduleApproval.Status.APPROVED)
        self.assertEqual(self.periods(), changed)
        self.assertNotEqual(self.periods(), self.original)
        self.assertTrue(all(e.status == ScheduleEntry.Status.APPROVED
                            for e in self.entries()))


class AmendmentApiTests(AmendmentBase):
    def test_full_cycle(self):
        self.approved_year()
        self.client.force_authenticate(self.employee)

        summary = self.client.get(
            f"/api/v1/employees/{self.employee.id}/annual-summary/?year=2027").json()
        self.assertTrue(summary["year_approved"])
        self.assertIsNone(summary["amendment_id"])

        opened = self.client.post(
            f"/api/v1/employees/{self.employee.id}/amendments/?year=2027")
        self.assertEqual(opened.status_code, 201, opened.content)
        amendment_id = opened.json()["id"]

        entry = self.entries().get(date_from=date(2027, 9, 6))
        self.client.delete(f"/api/v1/schedule-entries/{entry.id}/")
        self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-10-04", "date_to": "2027-10-17",
            "record_type": "vacation"}, format="json")

        submitted = self.client.post(f"/api/v1/amendments/{amendment_id}/submit/")
        self.assertEqual(submitted.status_code, 200, submitted.content)
        self.assertEqual(len(submitted.json()["steps"]), 2)

        for index, boss in enumerate([self.group_head, self.dept_head]):
            self.client.force_authenticate(boss)
            step_id = submitted.json()["steps"][index]["id"]
            result = self.client.post(
                f"/api/v1/schedule-approvals/{amendment_id}/steps/{step_id}/approve/",
                {"comment": "Согласовано"}, format="json")
            self.assertEqual(result.status_code, 200, result.content)

        self.assertEqual(result.json()["status"], "approved")

    def test_cancel_through_api(self):
        self.approved_year()
        original = self.periods()
        self.client.force_authenticate(self.employee)
        opened = self.client.post(
            f"/api/v1/employees/{self.employee.id}/amendments/?year=2027").json()

        entry = self.entries().get(date_from=date(2027, 9, 6))
        self.client.delete(f"/api/v1/schedule-entries/{entry.id}/")

        cancelled = self.client.post(f"/api/v1/amendments/{opened['id']}/cancel/")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.periods(), original)

    def test_open_without_approval_returns_409(self):
        self.plan()
        self.client.force_authenticate(self.employee)
        response = self.client.post(
            f"/api/v1/employees/{self.employee.id}/amendments/?year=2027")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "amendment_error")
