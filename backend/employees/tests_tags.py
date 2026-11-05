"""Справочник тегов, лимиты и ручное назначение (ФЗ 4.2.2, 4.7)."""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditLog
from company.models import CompanySettings
from employees.models import Employee, EmployeeTag, OrgUnit, Tag


class TagApiTests(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        self.client = APIClient()
        self.unit = OrgUnit.objects.create(name="Группа", unit_type=OrgUnit.UnitType.GROUP)
        self.other_unit = OrgUnit.objects.create(name="Чужая", unit_type=OrgUnit.UnitType.GROUP)
        self.admin = self.person("1", is_admin=True)
        self.manager = self.person("2", unit=self.unit)
        self.unit.manager = self.manager
        self.unit.save()
        self.employee = self.person("3", unit=self.unit)
        self.stranger = self.person("4", unit=self.other_unit)
        self.tag = Tag.objects.create(name="Дежурство", max_simultaneous_absence=2)
        self.org_tag = Tag.objects.create(name="Группа")
        EmployeeTag.objects.create(employee=self.employee, tag=self.org_tag,
                                   source=Tag.Source.AUTO_ORG)

    def person(self, number, unit=None, is_admin=False):
        employee = Employee.objects.create_user(
            email=f"p{number}@x.ru", employee_number=number, full_name=f"Сотрудник {number}",
            hire_date=date(2020, 1, 1), org_unit=unit)
        if is_admin:
            employee.is_admin = True
            employee.save()
        return employee

    def as_(self, who):
        self.client.force_authenticate(who)

    def test_list_with_counts(self):
        self.as_(self.employee)
        rows = {r["name"]: r for r in self.client.get("/api/v1/tags/").json()}
        self.assertEqual(rows["Группа"]["members_count"], 1)
        self.assertTrue(rows["Группа"]["is_org_unit_tag"])
        self.assertEqual(rows["Дежурство"]["max_simultaneous_absence"], 2)

    def test_manager_cannot_change_others_tag_limit(self):
        self.as_(self.manager)
        response = self.client.patch(f"/api/v1/tags/{self.tag.id}/",
                                     {"max_simultaneous_absence": 5}, format="json")
        self.assertEqual(response.status_code, 403)
        self.as_(self.admin)
        response = self.client.patch(f"/api/v1/tags/{self.tag.id}/",
                                     {"max_simultaneous_absence": 3}, format="json")
        self.assertEqual(response.status_code, 200)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.max_simultaneous_absence, 3)

    def test_limit_change_is_audited(self):
        self.as_(self.admin)
        self.client.patch(f"/api/v1/tags/{self.tag.id}/",
                          {"max_simultaneous_absence": 3}, format="json")
        self.assertTrue(AuditLog.objects.filter(entity_label__contains="Дежурство").exists())

    def test_clearing_limit_means_unlimited(self):
        self.as_(self.admin)
        self.client.patch(f"/api/v1/tags/{self.tag.id}/",
                          {"max_simultaneous_absence": None}, format="json")
        self.tag.refresh_from_db()
        self.assertIsNone(self.tag.max_simultaneous_absence)

    def test_admin_creates_tag(self):
        self.as_(self.admin)
        response = self.client.post("/api/v1/tags/", {
            "name": "Экспертиза Kubernetes", "max_simultaneous_absence": 1}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["members_count"], 0)

    def test_org_unit_tag_cannot_be_deleted(self):
        self.as_(self.admin)
        response = self.client.delete(f"/api/v1/tags/{self.org_tag.id}/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.delete(f"/api/v1/tags/{self.tag.id}/").status_code, 204)

    # --- Руководитель ведёт свои теги (решение от 06.11.2026) ----------------

    def manager_creates(self, name="Доступ в ЦОД", limit=1):
        self.as_(self.manager)
        return self.client.post("/api/v1/tags/", {"name": name,
                                 "max_simultaneous_absence": limit}, format="json")

    def test_manager_creates_tag_with_limit(self):
        response = self.manager_creates()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["created_by"], body["max_simultaneous_absence"], body["can_edit"]),
                         ("Сотрудник 2", 1, True))
        self.assertEqual(Tag.objects.get(name="Доступ в ЦОД").created_by, self.manager)

    def test_manager_edits_and_deletes_own_tag(self):
        tag_id = self.manager_creates().json()["id"]
        response = self.client.patch(f"/api/v1/tags/{tag_id}/",
                                     {"name": "ЦОД", "max_simultaneous_absence": 2}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/tags/{tag_id}/").status_code, 204)

    def test_manager_cannot_touch_other_managers_tag(self):
        other_manager = self.person("5", unit=self.other_unit)
        self.other_unit.manager = other_manager
        self.other_unit.save()
        tag_id = self.manager_creates().json()["id"]
        self.as_(other_manager)
        self.assertEqual(self.client.patch(f"/api/v1/tags/{tag_id}/",
                         {"max_simultaneous_absence": 9}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/tags/{tag_id}/").status_code, 403)
        row = next(r for r in self.client.get("/api/v1/tags/").json() if r["id"] == tag_id)
        self.assertFalse(row["can_edit"])

    def test_manager_cannot_delete_admin_or_org_tags(self):
        self.as_(self.manager)
        self.assertEqual(self.client.delete(f"/api/v1/tags/{self.tag.id}/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/tags/{self.org_tag.id}/").status_code, 403)

    def test_org_tag_not_editable_even_if_created_by_manager(self):
        """Тег, ставший тегом подразделения, — правило оргструктуры, не руководителя."""
        self.org_tag.created_by = self.manager
        self.org_tag.save()
        self.as_(self.manager)
        self.assertEqual(self.client.patch(f"/api/v1/tags/{self.org_tag.id}/",
                         {"max_simultaneous_absence": 1}, format="json").status_code, 403)

    def test_employee_cannot_create_tag(self):
        self.as_(self.employee)
        response = self.client.post("/api/v1/tags/", {"name": "Свой"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_admin_edits_manager_tag(self):
        tag_id = self.manager_creates().json()["id"]
        self.as_(self.admin)
        self.assertEqual(self.client.patch(f"/api/v1/tags/{tag_id}/",
                         {"max_simultaneous_absence": 4}, format="json").status_code, 200)
        rows = {r["id"]: r for r in self.client.get("/api/v1/tags/").json()}
        self.assertTrue(all(r["can_edit"] for r in rows.values()))

    def test_manager_tag_creation_is_audited(self):
        tag_id = self.manager_creates().json()["id"]
        self.assertTrue(AuditLog.objects.filter(entity_id=str(tag_id), changed_by=self.manager).exists())

    def test_manager_assigns_to_own_team_only(self):
        self.as_(self.manager)
        ok = self.client.post(f"/api/v1/tags/{self.tag.id}/assign/",
                              {"employee_id": str(self.employee.id)}, format="json")
        self.assertEqual(ok.status_code, 201)
        link = EmployeeTag.objects.get(employee=self.employee, tag=self.tag)
        self.assertEqual((link.source, link.assigned_by), (Tag.Source.MANUAL, self.manager))
        denied = self.client.post(f"/api/v1/tags/{self.tag.id}/assign/",
                                  {"employee_id": str(self.stranger.id)}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_employee_cannot_assign_even_to_self(self):
        """Решение от 25.10.2026: теги назначают только руководители и администратор."""
        self.as_(self.stranger)
        response = self.client.post(f"/api/v1/tags/{self.tag.id}/assign/",
                                    {"employee_id": str(self.stranger.id)}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmployeeTag.objects.filter(employee=self.stranger, tag=self.tag).exists())

    def test_employee_cannot_unassign_own_tag(self):
        EmployeeTag.objects.create(employee=self.employee, tag=self.tag,
                                   source=Tag.Source.MANUAL)
        self.as_(self.employee)
        response = self.client.delete(f"/api/v1/tags/{self.tag.id}/unassign/{self.employee.id}/")
        self.assertEqual(response.status_code, 403)

    def test_manager_unassign_outside_structure_forbidden(self):
        EmployeeTag.objects.create(employee=self.stranger, tag=self.tag,
                                   source=Tag.Source.MANUAL)
        self.as_(self.manager)
        response = self.client.delete(f"/api/v1/tags/{self.tag.id}/unassign/{self.stranger.id}/")
        self.assertEqual(response.status_code, 403)

    def test_org_unit_tag_cannot_be_unassigned(self):
        self.as_(self.admin)
        response = self.client.delete(
            f"/api/v1/tags/{self.org_tag.id}/unassign/{self.employee.id}/")
        self.assertEqual(response.status_code, 409)

    def test_unassign_manual(self):
        EmployeeTag.objects.create(employee=self.employee, tag=self.tag,
                                   source=Tag.Source.MANUAL)
        self.as_(self.manager)
        response = self.client.delete(f"/api/v1/tags/{self.tag.id}/unassign/{self.employee.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(EmployeeTag.objects.filter(employee=self.employee, tag=self.tag).exists())

    def test_members_limited_by_visibility(self):
        for person in (self.employee, self.stranger):
            EmployeeTag.objects.create(employee=person, tag=self.tag, source=Tag.Source.MANUAL)
        self.as_(self.manager)
        names = [r["full_name"] for r in self.client.get(
            f"/api/v1/tags/{self.tag.id}/members/").json()]
        self.assertEqual(names, ["Сотрудник 3"])

    def test_employee_search_respects_visibility(self):
        self.as_(self.manager)
        found = [r["employee_number"] for r in self.client.get(
            "/api/v1/employees/search/?q=Сотрудник").json()]
        self.assertNotIn("4", found)
        self.assertIn("3", found)
