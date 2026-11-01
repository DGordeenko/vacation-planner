"""Тесты импорта кадровой выгрузки."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from company.models import CompanySettings
from employees.importers import import_employees
from employees.models import Employee, Tag
from schedule.models import VacationLedger


def load(text, encoding="utf-8"):
    return import_employees(text.encode(encoding))


CLEAN = """employee_number;full_name;email;hire_date;department;group;manager_employee_number;initial_balance_days;extra_tags
1001;Начальник Отдела;chief@corp.ru;2015-01-01;Отдел Т;;;31.5;
1002;Тимлид Группы;lead@corp.ru;2016-01-01;Отдел Т;Группа Т;1001;24.0;Дежурная смена
1003;Инженер Первый;eng1@corp.ru;2018-01-01;Отдел Т;Группа Т;1002;12.25;
"""


class ImportBasicsTests(TestCase):
    def setUp(self):
        CompanySettings.get_solo()

    def test_clean_file_applies(self):
        report = load(CLEAN)
        self.assertTrue(report.applied, [p.message for p in report.problems])
        self.assertEqual(report.created, 3)
        self.assertEqual(Employee.objects.count(), 3)

    def test_hierarchy_is_derived_correctly(self):
        """Связь «сотрудник → руководитель» переводится в дерево оргединиц."""
        load(CLEAN)
        lead = Employee.objects.get(employee_number="1002")
        engineer = Employee.objects.get(employee_number="1003")
        self.assertEqual(lead.manager.employee_number, "1001")
        self.assertEqual(engineer.manager.employee_number, "1002")

    def test_visibility_follows_hierarchy(self):
        load(CLEAN)
        chief = Employee.objects.get(employee_number="1001")
        engineer = Employee.objects.get(employee_number="1003")
        self.assertEqual(len(chief.visible_employee_ids()), 3)
        self.assertEqual(len(engineer.visible_employee_ids()), 1)

    def test_reimport_updates_without_duplicates(self):
        load(CLEAN)
        report = load(CLEAN.replace("Тимлид Группы", "Тимлид Группы (новая фамилия)"))
        self.assertTrue(report.applied)
        self.assertEqual(report.created, 0)
        self.assertEqual(report.updated, 3)
        self.assertEqual(Employee.objects.count(), 3)
        self.assertIn("новая фамилия",
                      Employee.objects.get(employee_number="1002").full_name)

    def test_initial_balance_lands_in_ledger(self):
        load(CLEAN)
        row = VacationLedger.objects.get(
            employee__employee_number="1001",
            reason=VacationLedger.Reason.INITIAL_MIGRATION)
        self.assertEqual(row.days_delta, Decimal("31.50"))

    def test_initial_balance_dated_on_import_day(self):
        """Остаток задаётся на дату загрузки, и в этот день баланс равен ему:
        начисление за стаж до загрузки уже входит в остаток."""
        from schedule.calculations import balance_on
        load(CLEAN)
        employee = Employee.objects.get(employee_number="1001")
        row = VacationLedger.objects.get(
            employee=employee, reason=VacationLedger.Reason.INITIAL_MIGRATION)
        self.assertEqual(row.entry_date, date.today())
        self.assertEqual(balance_on(employee, date.today()), Decimal("31.50"))

    def test_tags_auto_and_manual(self):
        load(CLEAN)
        lead = Employee.objects.get(employee_number="1002")
        tags = {et.tag.name: et.source for et in lead.employee_tags.select_related("tag")}
        self.assertEqual(tags.get("Группа Т"), Tag.Source.AUTO_ORG)
        self.assertEqual(tags.get("Дежурная смена"), Tag.Source.MANUAL)

    def test_invitation_token_issued(self):
        load(CLEAN)
        self.assertTrue(all(
            e.invitation_token for e in Employee.objects.all()))


class ImportToleranceTests(TestCase):
    """Форматы, в которых реально приходят кадровые выгрузки."""

    def setUp(self):
        CompanySettings.get_solo()

    def test_russian_headers_and_comma_delimiter(self):
        report = load(
            "Таб_номер,ФИО,Почта,Дата_приема,department\n"
            "2001,Былин Василий,bylin@corp.ru,01.02.2014,Отдел Ц\n")
        self.assertTrue(report.applied, [p.message for p in report.problems])
        self.assertEqual(
            Employee.objects.get(employee_number="2001").hire_date, date(2014, 2, 1))

    def test_various_date_formats(self):
        report = load(
            "employee_number;full_name;email;hire_date;department\n"
            "2101;А;a@corp.ru;15/08/2020;Отдел Ц\n"
            "2102;Б;b@corp.ru;2021-11-30;Отдел Ц\n"
            "2103;В;c@corp.ru;03-04-2019;Отдел Ц\n")
        self.assertTrue(report.applied, [p.message for p in report.problems])
        self.assertEqual(
            Employee.objects.get(employee_number="2101").hire_date, date(2020, 8, 15))
        self.assertEqual(
            Employee.objects.get(employee_number="2103").hire_date, date(2019, 4, 3))

    def test_decimal_comma_in_balance(self):
        load("employee_number;full_name;email;hire_date;department;initial_balance_days\n"
             "2201;Г;g@corp.ru;2020-01-01;Отдел Ц;\"12,5\"\n")
        row = VacationLedger.objects.get(employee__employee_number="2201")
        self.assertEqual(row.days_delta, Decimal("12.50"))

    def test_cp1251_encoding(self):
        report = load(
            "employee_number;full_name;email;hire_date;department\n"
            "2301;Кириллица Тест;cp@corp.ru;2020-01-01;Отдел кодировок\n",
            encoding="cp1251")
        self.assertTrue(report.applied)
        self.assertEqual(
            Employee.objects.get(employee_number="2301").full_name, "Кириллица Тест")


class ImportRejectionTests(TestCase):
    """Файл применяется целиком или не применяется вовсе."""

    def setUp(self):
        CompanySettings.get_solo()

    def _codes(self, report):
        return [p.message for p in report.problems]

    def test_nothing_saved_when_any_row_broken(self):
        report = load(
            "employee_number;full_name;email;hire_date;department\n"
            "3001;Хороший;good@corp.ru;2018-05-05;Отдел П\n"
            "3002;;empty@corp.ru;2019-07-01;Отдел П\n")
        self.assertFalse(report.applied)
        self.assertEqual(Employee.objects.count(), 0)

    def test_duplicate_employee_number(self):
        report = load(
            "employee_number;full_name;email;hire_date;department\n"
            "3101;Первый;one@corp.ru;2018-01-01;Отдел П\n"
            "3101;Дубль;two@corp.ru;2018-01-01;Отдел П\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("повторяется" in m for m in self._codes(report)))

    def test_unknown_manager(self):
        report = load(
            "employee_number;full_name;email;hire_date;department;manager_employee_number\n"
            "3201;Кто-то;s@corp.ru;2018-01-01;Отдел П;9999\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("не найден" in m for m in self._codes(report)))

    def test_self_manager(self):
        report = load(
            "employee_number;full_name;email;hire_date;department;manager_employee_number\n"
            "3301;Сам себе;s@corp.ru;2018-01-01;Отдел П;3301\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("самого себя" in m for m in self._codes(report)))

    def test_cycle_detected(self):
        report = load(
            "employee_number;full_name;email;hire_date;department;manager_employee_number\n"
            "3401;Первый;a@corp.ru;2020-01-01;Отдел А;3402\n"
            "3402;Второй;b@corp.ru;2020-01-01;Отдел А;3401\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("Циклическое" in m for m in self._codes(report)))

    def test_bad_email_and_date(self):
        report = load(
            "employee_number;full_name;email;hire_date;department\n"
            "3501;Без собаки;bad-corp.ru;2020-01-01;Отдел П\n"
            "3502;Плохая дата;ok@corp.ru;не указана;Отдел П\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("@" in m for m in self._codes(report)))
        self.assertTrue(any("нераспознанном" in m for m in self._codes(report)))

    def test_negative_balance(self):
        report = load(
            "employee_number;full_name;email;hire_date;department;initial_balance_days\n"
            "3601;Минус;m@corp.ru;2020-01-01;Отдел П;-3\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("Отрицательный" in m for m in self._codes(report)))

    def test_termination_before_hire(self):
        report = load(
            "employee_number;full_name;email;hire_date;termination_date;department\n"
            "3701;Хроно;h@corp.ru;2020-01-01;2019-01-01;Отдел П\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("раньше даты приёма" in m for m in self._codes(report)))

    def test_missing_required_columns(self):
        report = load("Фамилия;Должность;Оклад\nИванов;Инженер;100000\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("обязательных колонок" in m for m in self._codes(report)))

    def test_cross_branch_reporting_rejected(self):
        """Подчинение через соседнюю ветвь дерево выразить не может."""
        report = load(
            "employee_number;full_name;email;hire_date;department;group;manager_employee_number\n"
            "3801;Шеф ЦОД;chief@corp.ru;2015-01-01;Отдел ЦОД;;\n"
            "3802;Инженер АРМ;arm@corp.ru;2019-01-01;Отдел поддержки;Группа АРМ;3801\n")
        self.assertFalse(report.applied)
        self.assertTrue(any("другой ветви" in m for m in self._codes(report)))
        self.assertEqual(Employee.objects.count(), 0)


class ImportDryRunTests(TestCase):
    def setUp(self):
        CompanySettings.get_solo()

    def test_dry_run_does_not_write(self):
        report = import_employees(CLEAN.encode("utf-8"), dry_run=True)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertEqual(report.total_rows, 3)

    def test_missing_balance_is_warning_not_error(self):
        report = load(
            "employee_number;full_name;email;hire_date;department\n"
            "4001;Без остатка;n@corp.ru;2018-01-01;Отдел П\n")
        self.assertTrue(report.applied)
        self.assertTrue(any("Остаток отпуска не указан" in w.message
                            for w in report.warnings))


class ImportApiTests(TestCase):
    """Экран загрузки: предпросмотр с итогами, затем применение (UX 2.5)."""

    def setUp(self):
        import base64

        from rest_framework.test import APIClient
        CompanySettings.get_solo()
        self.client = APIClient()
        self.admin = Employee.objects.create_user(
            email="adm@corp.ru", employee_number="0001", full_name="Админ",
            hire_date=date(2015, 1, 1))
        self.admin.is_admin = True
        self.admin.save()
        self.encode = lambda text: base64.b64encode(text.encode("utf-8")).decode()

    def post(self, text, dry_run):
        self.client.force_authenticate(self.admin)
        return self.client.post("/api/v1/employees/import-csv/",
                                {"content": self.encode(text), "dry_run": dry_run},
                                format="json")

    def test_preview_counts_without_writing(self):
        response = self.post(CLEAN, dry_run=True)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["created"], body["updated"], body["applied"]), (3, 0, False))
        self.assertFalse(Employee.objects.filter(employee_number="1001").exists())

    def test_apply_writes(self):
        body = self.post(CLEAN, dry_run=False).json()
        self.assertTrue(body["applied"])
        self.assertEqual(Employee.objects.filter(employee_number__in=["1001", "1002", "1003"])
                         .count(), 3)
        self.assertEqual(self.post(CLEAN, dry_run=True).json()["updated"], 3)

    def test_problems_return_conflict(self):
        response = self.post(CLEAN.replace("eng1@corp.ru", "без-собаки"), dry_run=True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["problems"][0]["row"], 4)

    def test_only_admin(self):
        self.client.force_authenticate(Employee.objects.create_user(
            email="u@corp.ru", employee_number="0002", full_name="Пользователь",
            hire_date=date(2015, 1, 1)))
        response = self.client.post("/api/v1/employees/import-csv/",
                                    {"content": self.encode(CLEAN)}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_garbage_is_rejected(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/v1/employees/import-csv/",
                                    {"content": "не base64!"}, format="json")
        self.assertEqual(response.status_code, 400)


class InvitationApiTests(TestCase):
    """Приглашения: список для рассылки, перевыпуск, вход по ссылке."""

    def setUp(self):
        import uuid

        from rest_framework.test import APIClient
        CompanySettings.get_solo()
        self.client = APIClient()
        self.admin = Employee.objects.create_user(
            email="adm@corp.ru", employee_number="0001", full_name="Админ",
            hire_date=date(2015, 1, 1))
        self.admin.is_admin = True
        self.admin.save()
        self.invited = Employee.objects.create_user(
            email="new@corp.ru", employee_number="0002", full_name="Новый Сотрудник",
            hire_date=date(2020, 1, 1))
        self.invited.invitation_token = uuid.uuid4()
        self.invited.save()
        self.fired = Employee.objects.create_user(
            email="old@corp.ru", employee_number="0003", full_name="Уволенный",
            hire_date=date(2010, 1, 1), status=Employee.Status.INACTIVE)
        self.fired.invitation_token = uuid.uuid4()
        self.fired.save()

    def accept(self, token, password):
        self.client.force_authenticate(None)
        return self.client.post("/api/v1/auth/accept-invitation/",
                                {"invitation_token": str(token), "password": password},
                                format="json")

    def test_list_only_active_pending(self):
        self.client.force_authenticate(self.admin)
        rows = self.client.get("/api/v1/admin/invitations/").json()
        self.assertEqual([r["full_name"] for r in rows], ["Новый Сотрудник"])
        self.assertEqual(rows[0]["invitation_token"], str(self.invited.invitation_token))

    def test_list_is_admin_only(self):
        self.client.force_authenticate(self.invited)
        self.assertEqual(self.client.get("/api/v1/admin/invitations/").status_code, 403)

    def test_accept_sets_password_and_leaves_list(self):
        response = self.accept(self.invited.invitation_token, "Otpusk-2027!")
        self.assertEqual(response.status_code, 200)
        self.invited.refresh_from_db()
        self.assertTrue(self.invited.check_password("Otpusk-2027!"))
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/v1/admin/invitations/").json(), [])

    def test_weak_password_rejected_and_link_kept(self):
        response = self.accept(self.invited.invitation_token, "12345")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "weak_password")
        self.invited.refresh_from_db()
        self.assertIsNotNone(self.invited.invitation_token)

    def test_fired_and_garbage_tokens_invalid(self):
        self.assertEqual(self.accept(self.fired.invitation_token, "Otpusk-2027!").status_code,
                         404)
        self.assertEqual(self.accept("не-токен", "Otpusk-2027!").status_code, 404)

    def test_reissue_invalidates_old_link(self):
        old = self.invited.invitation_token
        self.client.force_authenticate(self.admin)
        row = self.client.post(f"/api/v1/admin/invitations/{self.invited.id}/reissue/").json()
        self.assertNotEqual(row["invitation_token"], str(old))
        self.assertEqual(self.accept(old, "Otpusk-2027!").status_code, 404)
        self.assertEqual(self.accept(row["invitation_token"], "Otpusk-2027!").status_code, 200)


class LoginThrottleTests(TestCase):
    """Подбор пароля упирается в предел попыток."""

    def test_eleventh_attempt_in_a_minute_is_429(self):
        from django.core.cache import cache
        from rest_framework.test import APIClient
        cache.clear()
        client = APIClient()
        codes = [client.post("/api/v1/auth/login/", {"email": "x@y.ru", "password": "bad"},
                             format="json").status_code for _ in range(11)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10], 429)
        cache.clear()
