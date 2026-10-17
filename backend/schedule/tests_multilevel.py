"""Согласование через четыре уровня оргструктуры."""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from company.models import CompanySettings
from employees.models import Employee, OrgUnit
from schedule import approvals
from schedule.models import (
    ApprovalStep, Notification, ScheduleApproval, ScheduleEntry,
)
from schedule.tests import seed_rules


class FullHierarchyBase(TestCase):
    """Полная структура: дивизион → подразделение → отдел → группа."""

    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()

        self.division = OrgUnit.objects.create(
            name="Дивизион ИТ", unit_type=OrgUnit.UnitType.DIVISION)
        self.division_unit = OrgUnit.objects.create(
            name="Инфраструктура", unit_type=OrgUnit.UnitType.DIVISION_UNIT,
            parent=self.division)
        self.department = OrgUnit.objects.create(
            name="Отдел ЦОД", unit_type=OrgUnit.UnitType.DEPARTMENT,
            parent=self.division_unit)
        self.group = OrgUnit.objects.create(
            name="Группа эксплуатации", unit_type=OrgUnit.UnitType.GROUP,
            parent=self.department)

        def boss(number, name, unit):
            person = Employee.objects.create_user(
                email=f"{number}@x.ru", employee_number=number,
                full_name=name, hire_date=date(2015, 1, 1), org_unit=unit)
            unit.manager = person
            unit.save()
            return person

        self.division_head = boss("D1", "Дивизионов Дмитрий", self.division)
        self.unit_head = boss("P1", "Подразделенцев Пётр", self.division_unit)
        self.dept_head = boss("O1", "Отделов Олег", self.department)
        self.group_head = boss("G1", "Группин Геннадий", self.group)

        self.employee = Employee.objects.create_user(
            email="emp@x.ru", employee_number="E1", full_name="Иванов Иван",
            hire_date=date(2020, 1, 1), org_unit=self.group, password="pw12345")

        self.admin = Employee.objects.create_user(
            email="adm@x.ru", employee_number="A1", full_name="Админов Админ",
            hire_date=date(2016, 1, 1), org_unit=self.department)
        self.admin.is_admin = True
        self.admin.save()

    def plan(self, employee=None):
        target = employee or self.employee
        for start, end in [(date(2027, 7, 5), date(2027, 7, 18)),
                           (date(2027, 9, 6), date(2027, 9, 19))]:
            ScheduleEntry.objects.create(
                employee=target, created_by=target,
                record_type=ScheduleEntry.RecordType.VACATION,
                date_from=start, date_to=end)


class ChainBuildingTests(FullHierarchyBase):
    def test_four_levels_in_order(self):
        chain = approvals.build_chain(self.employee)
        self.assertEqual(
            [person.employee_number for person, _ in chain],
            ["G1", "O1", "P1", "D1"])
        self.assertEqual(
            [level for _, level in chain],
            [ApprovalStep.Level.GROUP, ApprovalStep.Level.DEPARTMENT,
             ApprovalStep.Level.DIVISION_UNIT, ApprovalStep.Level.DIVISION])

    def test_manager_chain_starts_above_himself(self):
        """Руководитель группы не согласует собственный график."""
        chain = approvals.build_chain(self.group_head)
        self.assertEqual(
            [person.employee_number for person, _ in chain], ["O1", "P1", "D1"])

    def test_division_head_falls_back_to_admin(self):
        """У вершины структуры согласующим становится администратор."""
        chain = approvals.build_chain(self.division_head)
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0][0], self.admin)
        self.assertEqual(chain[0][1], ApprovalStep.Level.ADMIN)

    def test_same_person_appears_once(self):
        """Один руководитель на двух уровнях не согласует дважды."""
        self.division_unit.manager = self.dept_head
        self.division_unit.save()
        chain = approvals.build_chain(self.employee)
        numbers = [person.employee_number for person, _ in chain]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(numbers, ["G1", "O1", "D1"])

    def test_levels_setting_is_respected(self):
        settings_row = CompanySettings.get_solo()
        settings_row.approval_levels = 2
        settings_row.save()
        chain = approvals.build_chain(self.employee)
        self.assertEqual(
            [person.employee_number for person, _ in chain], ["G1", "O1"])

    def test_shorter_structure_gives_shorter_chain(self):
        """Уровней меньше четырёх — цепочка короче, администратор не нужен."""
        flat = OrgUnit.objects.create(
            name="Плоский отдел", unit_type=OrgUnit.UnitType.DEPARTMENT)
        head = Employee.objects.create_user(
            email="flat@x.ru", employee_number="F1", full_name="Плоский Шеф",
            hire_date=date(2018, 1, 1), org_unit=flat)
        flat.manager = head
        flat.save()
        worker = Employee.objects.create_user(
            email="fw@x.ru", employee_number="F2", full_name="Плоский Работник",
            hire_date=date(2020, 1, 1), org_unit=flat)

        chain = approvals.build_chain(worker)
        self.assertEqual([person.employee_number for person, _ in chain], ["F1"])
        self.assertNotIn(self.admin, [person for person, _ in chain])


class SequentialDecisionTests(FullHierarchyBase):
    def submit(self):
        self.plan()
        return approvals.submit_year(self.employee, 2027, self.employee)

    def test_four_steps_created_pending(self):
        approval = self.submit()
        self.assertEqual(approval.steps.count(), 4)
        self.assertEqual(
            list(approval.steps.values_list("order", flat=True)), [1, 2, 3, 4])
        self.assertTrue(all(step.status == ScheduleApproval.Status.PENDING
                            for step in approval.steps.all()))

    def test_only_first_approver_is_notified(self):
        self.submit()
        recipients = set(Notification.objects.filter(
            notification_type=Notification.NotificationType.APPROVAL_REQUIRED
        ).values_list("recipient__employee_number", flat=True))
        self.assertEqual(recipients, {"G1"})

    def test_cannot_skip_a_level(self):
        approval = self.submit()
        third = approval.steps.get(order=3)
        with self.assertRaises(approvals.ApprovalError) as ctx:
            approvals.decide_step(third, self.unit_head, approved=True)
        self.assertIn("предыдущем уровне", str(ctx.exception))

    def test_next_level_is_notified_after_each_decision(self):
        approval = self.submit()
        approvals.decide_step(approval.steps.get(order=1), self.group_head,
                              approved=True)
        recipients = set(Notification.objects.filter(
            notification_type=Notification.NotificationType.APPROVAL_REQUIRED
        ).values_list("recipient__employee_number", flat=True))
        self.assertEqual(recipients, {"G1", "O1"})

    def test_schedule_approved_only_after_last_level(self):
        approval = self.submit()
        bosses = [self.group_head, self.dept_head, self.unit_head, self.division_head]
        for order, boss in enumerate(bosses, start=1):
            step = approval.steps.get(order=order)
            approvals.decide_step(step, boss, approved=True)
            approval.refresh_from_db()
            if order < 4:
                self.assertEqual(approval.status, ScheduleApproval.Status.PENDING,
                                 f"после уровня {order} график не должен быть утверждён")
                self.assertFalse(ScheduleEntry.objects.filter(
                    employee=self.employee,
                    status=ScheduleEntry.Status.APPROVED).exists())

        self.assertEqual(approval.status, ScheduleApproval.Status.APPROVED)
        self.assertEqual(ScheduleEntry.objects.filter(
            employee=self.employee,
            status=ScheduleEntry.Status.APPROVED).count(), 2)

    def test_rejection_on_third_level_stops_everything(self):
        approval = self.submit()
        approvals.decide_step(approval.steps.get(order=1), self.group_head, True)
        approvals.decide_step(approval.steps.get(order=2), self.dept_head, True)
        approvals.decide_step(approval.steps.get(order=3), self.unit_head,
                              approved=False, comment="Перенесите июль")

        approval.refresh_from_db()
        self.assertEqual(approval.status, ScheduleApproval.Status.REJECTED)
        self.assertEqual(
            approval.steps.get(order=4).status, ScheduleApproval.Status.PENDING)
        self.assertEqual(ScheduleEntry.objects.filter(
            employee=self.employee, status=ScheduleEntry.Status.DRAFT).count(), 2)

    def test_wrong_approver_rejected_even_at_right_step(self):
        approval = self.submit()
        with self.assertRaises(approvals.ApprovalError):
            approvals.decide_step(approval.steps.get(order=1), self.dept_head, True)

    def test_admin_can_decide_any_step(self):
        approval = self.submit()
        approvals.decide_step(approval.steps.get(order=1), self.admin, True)
        self.assertEqual(approval.steps.get(order=1).status,
                         ScheduleApproval.Status.APPROVED)

    def test_withdraw_cancels_whole_chain(self):
        approval = self.submit()
        approvals.decide_step(approval.steps.get(order=1), self.group_head, True)
        approvals.withdraw(approval, self.employee)

        approval.refresh_from_db()
        self.assertEqual(approval.status, ScheduleApproval.Status.WITHDRAWN)
        self.assertEqual(ScheduleEntry.objects.filter(
            employee=self.employee, status=ScheduleEntry.Status.DRAFT).count(), 2)


class MultiLevelApiTests(FullHierarchyBase):
    def test_full_chain_through_api(self):
        self.plan()
        self.client.force_authenticate(self.employee)
        response = self.client.post(
            f"/api/v1/employees/{self.employee.id}/submit-year/?year=2027")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(len(body["steps"]), 4)
        self.assertEqual([step["approver_name"] for step in body["steps"]],
                         ["Группин Геннадий", "Отделов Олег",
                          "Подразделенцев Пётр", "Дивизионов Дмитрий"])

        approval_id = body["id"]
        bosses = [self.group_head, self.dept_head, self.unit_head, self.division_head]
        for index, boss in enumerate(bosses):
            self.client.force_authenticate(boss)
            step_id = body["steps"][index]["id"]
            result = self.client.post(
                f"/api/v1/schedule-approvals/{approval_id}/steps/{step_id}/approve/",
                {"comment": "Согласовано"}, format="json")
            self.assertEqual(result.status_code, 200, result.content)
            expected = "approved" if index == 3 else "pending"
            self.assertEqual(result.json()["status"], expected)

    def test_skipping_level_returns_409(self):
        self.plan()
        self.client.force_authenticate(self.employee)
        body = self.client.post(
            f"/api/v1/employees/{self.employee.id}/submit-year/?year=2027").json()

        self.client.force_authenticate(self.unit_head)
        result = self.client.post(
            f"/api/v1/schedule-approvals/{body['id']}"
            f"/steps/{body['steps'][2]['id']}/approve/", {}, format="json")
        self.assertEqual(result.status_code, 409)

    def test_each_approver_sees_pending_schedule(self):
        self.plan()
        approvals.submit_year(self.employee, 2027, self.employee)
        for boss in [self.group_head, self.dept_head, self.unit_head, self.division_head]:
            self.client.force_authenticate(boss)
            body = self.client.get("/api/v1/schedule-approvals/?status=pending").json()
            self.assertEqual(body["count"], 1, f"{boss.full_name} не видит согласование")
