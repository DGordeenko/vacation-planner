"""Тесты административного календаря праздников."""

from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from company.importers import import_holidays
from company.models import CompanySettings, Holiday
from employees.models import Employee
from schedule.models import ScheduleEntry


def load(text, **kwargs):
    return import_holidays(text.encode("utf-8"), **kwargs)


CLEAN = """date;name;day_type
2027-01-01;Новый год;holiday
2027-01-07;Рождество;holiday
2027-02-22;Предпраздничный;pre_holiday_short
2027-05-09;День Победы;holiday
"""


class HolidayImportTests(TestCase):
    def test_clean_file_applies(self):
        report = load(CLEAN)
        self.assertTrue(report.applied, [p.message for p in report.problems])
        self.assertEqual(report.created, 4)
        self.assertEqual(Holiday.objects.count(), 4)

    def test_day_type_defaults_to_holiday(self):
        load("date;name\n2027-03-08;Женский день\n")
        self.assertEqual(
            Holiday.objects.get(date=date(2027, 3, 8)).day_type,
            Holiday.DayType.HOLIDAY)

    def test_russian_headers_and_types(self):
        report = load("Дата,Название,Тип\n"
                      "01.01.2027,Новый год,праздничный\n"
                      "22.02.2027,Короткий день,сокращённый\n")
        self.assertTrue(report.applied, [p.message for p in report.problems])
        self.assertEqual(
            Holiday.objects.get(date=date(2027, 2, 22)).day_type,
            Holiday.DayType.PRE_HOLIDAY_SHORT)

    def test_cp1251(self):
        report = import_holidays(
            "date;name\n2027-01-01;Новый год\n".encode("cp1251"))
        self.assertTrue(report.applied)
        self.assertEqual(Holiday.objects.get(date=date(2027, 1, 1)).name, "Новый год")

    def test_duplicate_date_rejected(self):
        report = load("date;name\n2027-01-01;Новый год\n2027-01-01;Дубль\n")
        self.assertFalse(report.applied)
        self.assertEqual(Holiday.objects.count(), 0)

    def test_unknown_day_type_rejected(self):
        report = load("date;name;day_type\n2027-01-01;Новый год;выдуманный\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("Неизвестный тип" in p.message for p in report.problems))

    def test_bad_date_rejected(self):
        report = load("date;name\nне дата;Новый год\n")
        self.assertFalse(report.applied)
        self.assertEqual(Holiday.objects.count(), 0)

    def test_wrong_year_rejected(self):
        report = load("date;name\n2028-01-01;Новый год\n", year=2027)
        self.assertFalse(report.applied)
        self.assertTrue(any("2028" in p.message for p in report.problems))

    def test_missing_columns(self):
        report = load("колонка;другая\n1;2\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("обязательной колонки" in p.message
                            for p in report.problems))

    def test_dry_run_does_not_write(self):
        report = load(CLEAN, dry_run=True)
        self.assertEqual(Holiday.objects.count(), 0)
        self.assertEqual(len(report.preview), 4)

    def test_reimport_updates_existing(self):
        load(CLEAN)
        report = load("date;name\n2027-01-01;Новый год (уточнено)\n")
        self.assertEqual(report.updated, 1)
        self.assertEqual(Holiday.objects.count(), 4)
        self.assertIn("уточнено", Holiday.objects.get(date=date(2027, 1, 1)).name)

    def test_replace_clears_year(self):
        load(CLEAN)
        report = load("date;name\n2027-06-12;День России\n", replace=True)
        self.assertTrue(report.applied)
        self.assertEqual(Holiday.objects.filter(date__year=2027).count(), 1)


class HolidayAdminApiTests(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        self.client = APIClient()
        self.admin = Employee.objects.create_user(
            email="admin@c.ru", employee_number="A1", full_name="Админ",
            hire_date=date(2015, 1, 1))
        self.admin.is_admin = True
        self.admin.save()
        self.user = Employee.objects.create_user(
            email="user@c.ru", employee_number="U1", full_name="Сотрудник",
            hire_date=date(2020, 1, 1))

    def test_employee_can_read_calendar(self):
        Holiday.objects.create(date=date(2027, 1, 1), name="Новый год",
                               day_type=Holiday.DayType.HOLIDAY)
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/admin/holidays/?year=2027")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

    def test_employee_cannot_create(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/v1/admin/holidays/", {
            "date": "2027-01-01", "name": "Свой праздник",
            "day_type": "holiday"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Holiday.objects.count(), 0)

    def test_admin_can_create_and_delete(self):
        self.client.force_authenticate(self.admin)
        created = self.client.post("/api/v1/admin/holidays/", {
            "date": "2027-01-01", "name": "Новый год",
            "day_type": "holiday"}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["weekday"], 4)  # 1 января 2027 — пятница

        removed = self.client.delete(f"/api/v1/admin/holidays/{created.json()['id']}/")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(Holiday.objects.count(), 0)

    def test_import_through_api(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/holidays/import/", {
            "content": CLEAN, "year": 2027}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["applied"])
        self.assertEqual(response.json()["created"], 4)

    def test_import_preview_does_not_write(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/holidays/import/", {
            "content": CLEAN, "dry_run": True}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["applied"])
        self.assertEqual(len(response.json()["preview"]), 4)
        self.assertEqual(Holiday.objects.count(), 0)

    def test_import_reports_problems(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/holidays/import/", {
            "content": "date;name\nкривая;Праздник\n"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["problems"])

    def test_copy_year(self):
        Holiday.objects.create(date=date(2027, 1, 1), name="Новый год",
                               day_type=Holiday.DayType.HOLIDAY)
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/holidays/copy-year/", {
            "source_year": 2027, "target_year": 2028}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["created"], 1)
        self.assertTrue(Holiday.objects.filter(date=date(2028, 1, 1)).exists())

    def test_copy_year_refuses_to_overwrite(self):
        Holiday.objects.create(date=date(2027, 1, 1), name="Новый год",
                               day_type=Holiday.DayType.HOLIDAY)
        Holiday.objects.create(date=date(2028, 5, 9), name="День Победы",
                               day_type=Holiday.DayType.HOLIDAY)
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/holidays/copy-year/", {
            "source_year": 2027, "target_year": 2028}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "target_year_exists")

    def test_copy_year_skips_29_february(self):
        Holiday.objects.create(date=date(2028, 2, 29), name="Високосный",
                               day_type=Holiday.DayType.HOLIDAY)
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/admin/holidays/copy-year/", {
            "source_year": 2028, "target_year": 2029}, format="json")
        self.assertEqual(response.json()["skipped"], ["2028-02-29"])

    def test_years_summary(self):
        Holiday.objects.create(date=date(2027, 1, 1), name="A",
                               day_type=Holiday.DayType.HOLIDAY)
        Holiday.objects.create(date=date(2028, 1, 1), name="B",
                               day_type=Holiday.DayType.HOLIDAY)
        self.client.force_authenticate(self.admin)
        body = self.client.get("/api/v1/admin/holidays/years/").json()
        self.assertEqual({row["year"] for row in body}, {2027, 2028})

    def test_impact_shows_affected_entries(self):
        """Правка календаря не пересчитывает отпуска — масштаб должен быть виден."""
        employee = Employee.objects.create_user(
            email="e@c.ru", employee_number="E1", full_name="Отпускник",
            hire_date=date(2020, 1, 1))
        ScheduleEntry.objects.create(
            employee=employee, created_by=employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 5), date_to=date(2027, 7, 18))
        self.client.force_authenticate(self.admin)
        body = self.client.get("/api/v1/admin/holidays/impact/?year=2027").json()
        self.assertEqual(body["affected_entries"], 1)
        self.assertEqual(body["affected_employees"], 1)

    def test_clear_year(self):
        Holiday.objects.create(date=date(2027, 1, 1), name="A",
                               day_type=Holiday.DayType.HOLIDAY)
        self.client.force_authenticate(self.admin)
        response = self.client.delete("/api/v1/admin/holidays/clear-year/?year=2027")
        self.assertEqual(response.json()["deleted"], 1)
        self.assertEqual(Holiday.objects.count(), 0)
