"""Тесты журнала изменений."""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from audit.context import acting_as
from audit.models import AuditLog
from company.models import CompanySettings, Holiday
from employees.models import Employee, OrgUnit
from schedule import approvals
from schedule.models import ScheduleEntry, ValidationRule
from schedule.tests import seed_rules


class AuditBase(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()

        self.department = OrgUnit.objects.create(
            name="Отдел А", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.chief = Employee.objects.create_user(
            email="chief@a.ru", employee_number="C1", full_name="Руководитель",
            hire_date=date(2015, 1, 1), org_unit=self.department)
        self.department.manager = self.chief
        self.department.save()

        self.group = OrgUnit.objects.create(
            name="Группа А", unit_type=OrgUnit.UnitType.GROUP, parent=self.department)
        self.employee = Employee.objects.create_user(
            email="emp@a.ru", employee_number="E1", full_name="Сотрудник",
            hire_date=date(2020, 1, 1), org_unit=self.group, password="pw12345")

        self.admin = Employee.objects.create_user(
            email="adm@a.ru", employee_number="A1", full_name="Админ",
            hire_date=date(2016, 1, 1), org_unit=self.department)
        self.admin.is_admin = True
        self.admin.save()

        AuditLog.objects.all().delete()  # чистим записи от подготовки данных


class RecordingTests(AuditBase):
    def test_create_is_recorded(self):
        self.client.force_authenticate(self.employee)
        self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-07-05", "date_to": "2027-07-18",
            "record_type": "vacation"}, format="json")

        row = AuditLog.objects.get(entity_type="ScheduleEntry",
                                   action=AuditLog.Action.CREATE)
        self.assertEqual(row.changed_by, self.employee)
        self.assertEqual(row.changed_by_name, "Сотрудник")
        self.assertEqual(row.subject_employee, self.employee)
        self.assertEqual(row.source, "api")
        self.assertIn("Отпуск", row.entity_label)

    def test_update_records_before_and_after(self):
        entry = ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 5), date_to=date(2027, 7, 18))
        AuditLog.objects.all().delete()

        entry.status = ScheduleEntry.Status.APPROVED
        entry.save()

        row = AuditLog.objects.get(action=AuditLog.Action.UPDATE)
        self.assertEqual(row.diff["status"]["was"], "draft")
        self.assertEqual(row.diff["status"]["became"], "approved")

    def test_save_without_changes_is_not_recorded(self):
        """Журнал не засоряется пустыми сохранениями."""
        entry = ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 5), date_to=date(2027, 7, 18))
        AuditLog.objects.all().delete()
        entry.save()
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_delete_keeps_snapshot(self):
        entry = ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 5), date_to=date(2027, 7, 18))
        AuditLog.objects.all().delete()
        entry.delete()

        row = AuditLog.objects.get(action=AuditLog.Action.DELETE)
        self.assertEqual(row.diff["before"]["date_from"], "2027-07-05")

    def test_password_never_lands_in_log(self):
        self.employee.set_password("newsecret123")
        self.employee.full_name = "Сотрудник Обновлённый"
        self.employee.save()

        rows = AuditLog.objects.filter(entity_type="Employee")
        for row in rows:
            self.assertNotIn("password", str(row.diff))
            self.assertNotIn("newsecret", str(row.diff))

    def test_author_name_survives_account_removal(self):
        """Имя автора хранится строкой — история переживает удаление учётки."""
        with acting_as(self.chief, source="api"):
            Holiday.objects.create(date=date(2027, 1, 1), name="Новый год",
                                   day_type=Holiday.DayType.HOLIDAY)
        row = AuditLog.objects.get(entity_type="Holiday")
        self.assertEqual(row.changed_by_name, "Руководитель")

        self.chief.audit_records.update(changed_by=None)
        row.refresh_from_db()
        self.assertIsNone(row.changed_by)
        self.assertEqual(row.changed_by_name, "Руководитель")

    def test_exception_approval_is_recorded(self):
        entry = ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 3, 2), date_to=date(2027, 3, 4))
        AuditLog.objects.all().delete()

        with acting_as(self.chief, source="api"):
            approvals.approve_exception(
                entry, "no_weekend_captured", self.chief, "Разрешаю в виде исключения")

        row = AuditLog.objects.get(entity_type="ExceptionApproval")
        self.assertEqual(row.changed_by_name, "Руководитель")
        self.assertIn("Разрешаю", str(row.diff))

    def test_rule_threshold_change_is_recorded(self):
        """Изменение порога правила меняет поведение системы — нужен след."""
        rule = ValidationRule.objects.get(code="min_annual_days")
        AuditLog.objects.all().delete()
        with acting_as(self.admin, source="api"):
            rule.threshold_value = 30
            rule.save()

        row = AuditLog.objects.get(entity_type="ValidationRule")
        self.assertEqual(float(row.diff["threshold_value"]["was"]), 28.0)
        self.assertEqual(float(row.diff["threshold_value"]["became"]), 30.0)

    def test_import_marked_as_source(self):
        from employees.importers import import_employees
        with acting_as(self.admin, source="api"):
            import_employees(
                "employee_number;full_name;email;hire_date;department\n"
                "Z1;Новый Сотрудник;z@a.ru;2020-01-01;Отдел Z\n".encode("utf-8"))
        row = AuditLog.objects.filter(
            entity_type="Employee", action=AuditLog.Action.CREATE).first()
        self.assertEqual(row.source, "import")


class AuditVisibilityTests(AuditBase):
    def setUp(self):
        super().setUp()
        with acting_as(self.employee, source="api"):
            ScheduleEntry.objects.create(
                employee=self.employee, created_by=self.employee,
                record_type=ScheduleEntry.RecordType.VACATION,
                date_from=date(2027, 7, 5), date_to=date(2027, 7, 18))

        self.outsider = Employee.objects.create_user(
            email="out@a.ru", employee_number="O1", full_name="Посторонний",
            hire_date=date(2020, 1, 1))
        with acting_as(self.outsider, source="api"):
            ScheduleEntry.objects.create(
                employee=self.outsider, created_by=self.outsider,
                record_type=ScheduleEntry.RecordType.VACATION,
                date_from=date(2027, 8, 2), date_to=date(2027, 8, 15))

    def test_admin_sees_everything(self):
        self.client.force_authenticate(self.admin)
        body = self.client.get("/api/v1/audit-log/?entity_type=ScheduleEntry").json()
        self.assertEqual(body["count"], 2)

    def test_manager_sees_own_structure(self):
        self.client.force_authenticate(self.chief)
        body = self.client.get("/api/v1/audit-log/?entity_type=ScheduleEntry").json()
        names = {row["subject_name"] for row in body["results"]}
        self.assertIn("Сотрудник", names)
        self.assertNotIn("Посторонний", names)

    def test_employee_sees_only_own(self):
        self.client.force_authenticate(self.employee)
        body = self.client.get("/api/v1/audit-log/?entity_type=ScheduleEntry").json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["subject_name"], "Сотрудник")

    def test_log_is_read_only(self):
        self.client.force_authenticate(self.admin)
        row = AuditLog.objects.first()
        self.assertEqual(
            self.client.delete(f"/api/v1/audit-log/{row.id}/").status_code, 405)
        self.assertEqual(
            self.client.post("/api/v1/audit-log/", {}, format="json").status_code, 405)

    def test_filters(self):
        self.client.force_authenticate(self.admin)
        by_employee = self.client.get(
            f"/api/v1/audit-log/?employee_id={self.employee.id}").json()
        self.assertTrue(all(row["subject_employee"] == str(self.employee.id)
                            for row in by_employee["results"]))
        by_action = self.client.get("/api/v1/audit-log/?action=create").json()
        self.assertTrue(all(row["action"] == "create" for row in by_action["results"]))


class AuditTrailTests(AuditBase):
    def test_full_approval_history_is_reconstructable(self):
        """Главный сценарий: восстановить, кто и почему разрешил отпуск."""
        self.client.force_authenticate(self.employee)
        for start, end in [("2027-07-05", "2027-07-18"), ("2027-09-06", "2027-09-19")]:
            self.client.post("/api/v1/schedule-entries/", {
                "date_from": start, "date_to": end,
                "record_type": "vacation"}, format="json")

        response = self.client.post(
            f"/api/v1/employees/{self.employee.id}/submit-year/?year=2027")
        approval_id = response.json()["id"]
        step_id = response.json()["steps"][0]["id"]

        self.client.force_authenticate(self.chief)
        self.client.post(
            f"/api/v1/schedule-approvals/{approval_id}/steps/{step_id}/approve/",
            {"comment": "Утверждаю"}, format="json")

        trail = AuditLog.objects.filter(
            subject_employee=self.employee).order_by("changed_at")
        types = [row.entity_type for row in trail]
        self.assertIn("ScheduleEntry", types)
        self.assertIn("ScheduleApproval", types)
        self.assertIn("ApprovalStep", types)

        decision = trail.filter(entity_type="ApprovalStep",
                                action=AuditLog.Action.UPDATE).last()
        self.assertEqual(decision.changed_by_name, "Руководитель")
        self.assertIn("Утверждаю", str(decision.diff))
