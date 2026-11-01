"""Права на изменение записей и устойчивость к неверным параметрам.

Найдено при проверке перед публичным выпуском: сотрудник мог одним PATCH
«утвердить» себе отпуск и сдвинуть даты в обход проверок, а руководитель —
удалить запись подчинённого.
"""

from datetime import date
from decimal import Decimal

from schedule import calculations
from schedule.models import ScheduleEntry
from schedule.tests_approvals import ApprovalBase


class EntryChangeRightsTests(ApprovalBase):
    def test_entries_cannot_be_patched(self):
        entry = self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))
        self.client.force_authenticate(self.employee)
        response = self.client.patch(f"/api/v1/schedule-entries/{entry.id}/",
                                     {"status": "approved", "date_to": "2027-08-30"},
                                     format="json")
        self.assertEqual(response.status_code, 405)
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.date_to),
                         (ScheduleEntry.Status.DRAFT, date(2027, 7, 18)))

    def test_status_ignored_on_create(self):
        self.client.force_authenticate(self.employee)
        body = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-07-05", "date_to": "2027-07-18", "record_type": "vacation",
            "status": "approved"}, format="json").json()
        self.assertEqual(body["status"], "draft")

    def test_manager_cannot_delete_subordinate_entry(self):
        entry = self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))
        self.client.force_authenticate(self.chief)
        self.assertEqual(self.client.delete(f"/api/v1/schedule-entries/{entry.id}/").status_code,
                         403)
        self.assertTrue(ScheduleEntry.objects.filter(id=entry.id).exists())

    def test_submitted_entry_cannot_be_deleted(self):
        entry = self.add_vacation(date(2027, 7, 5), date(2027, 7, 18),
                                  status=ScheduleEntry.Status.PENDING_APPROVAL)
        self.client.force_authenticate(self.employee)
        response = self.client.delete(f"/api/v1/schedule-entries/{entry.id}/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "entry_locked")

    def test_owner_deletes_draft(self):
        entry = self.add_vacation(date(2027, 7, 5), date(2027, 7, 18))
        self.client.force_authenticate(self.employee)
        self.assertEqual(self.client.delete(f"/api/v1/schedule-entries/{entry.id}/").status_code,
                         204)

    def test_approved_day_off_not_cancelled_by_employee(self):
        rvd = ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.admin,
            record_type=ScheduleEntry.RecordType.RVD, date_from=date(2027, 3, 6),
            date_to=date(2027, 3, 6), work_hours=Decimal("8"),
            rvd_compensation_type=ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF,
            status=ScheduleEntry.Status.APPROVED)
        calculations.sync_rvd_accrual(rvd)
        self.client.force_authenticate(self.employee)
        day_off = self.client.post("/api/v1/day-offs/", {"date_from": "2027-03-10"},
                                   format="json").json()
        self.client.force_authenticate(self.chief)
        self.client.post(f"/api/v1/day-offs/{day_off['id']}/approve/")
        self.client.force_authenticate(self.employee)
        response = self.client.delete(f"/api/v1/day-offs/{day_off['id']}/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(calculations.time_off_balance_hours(self.employee), Decimal("0.00"))

    def test_foreign_day_off_delete_is_403_not_500(self):
        day_off = ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.DAY_OFF, date_from=date(2027, 3, 10),
            date_to=date(2027, 3, 10), status=ScheduleEntry.Status.PENDING_APPROVAL)
        self.client.force_authenticate(self.chief)
        self.assertEqual(self.client.delete(f"/api/v1/day-offs/{day_off.id}/").status_code, 403)


class BadParametersTests(ApprovalBase):
    """Мусор в параметрах — 400 с пояснением, а не «ошибка сервера»."""

    def test_bad_params_are_400(self):
        self.client.force_authenticate(self.admin)
        for url in [
            "/api/v1/schedule-entries/?employee_id=abc",
            "/api/v1/schedule-entries/?date_from=вчера",
            f"/api/v1/employees/{self.employee.id}/annual-summary/?year=двадцать",
            f"/api/v1/employees/{self.employee.id}/balance/?date=31.12.2027",
            "/api/v1/admin/holidays/?year=x",
            "/api/v1/audit-log/?until=завтра",
            "/api/v1/audit-log/?since=завтра",
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 400)
        response = self.client.post("/api/v1/day-offs/validate/", {"date": "10.03.2027"},
                                    format="json")
        self.assertEqual(response.status_code, 400)
