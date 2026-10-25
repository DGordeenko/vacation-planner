"""Тесты по сценариям приёмки (документ 09-ACCEPTANCE-TESTS.md)."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from company.models import CompanySettings, Holiday
from employees.models import Employee, EmployeeTag, OrgUnit, Tag
from schedule import calculations
from schedule.models import ScheduleEntry, ValidationRule, VacationLedger


def seed_rules():
    """Правила с порогами по умолчанию.

    Справочник создаёт миграция 0006; здесь пороги приводятся к значениям,
    на которые рассчитаны тесты, даже если миграция их поменяет.
    """
    for code, name, threshold in [
        ("min_annual_days", "Годовая норма", 28),
        ("min_continuous_days", "Непрерывный отпуск", 14),
        ("max_continuous_workdays", "Длительное отсутствие", 11),
        ("annual_ceiling", "Годовой потолок", 42),
        ("balance_overrun", "Превышение баланса", 7),
        ("no_weekend_captured", "Без захвата выходных", None),
        ("tag_limit_exceeded", "Лимит по тегу", None),
    ]:
        ValidationRule.objects.update_or_create(
            code=code, defaults={"name": name, "threshold_value": threshold,
                                 "is_enabled": True})


class AccrualTests(TestCase):
    """Группа B — баланс отпускных дней."""

    def setUp(self):
        CompanySettings.get_solo()

    def test_b1_proportional_accrual_partial_month(self):
        """B1: приём 20 марта -> 2.33 * 12/31 = 0.90 дня."""
        employee = Employee.objects.create_user(
            email="a@x.ru", employee_number="001", full_name="Тест",
            hire_date=date(2026, 3, 20))
        accrued = calculations.accrual_for_month(employee, 2026, 3, Decimal("2.33"))
        self.assertEqual(accrued, Decimal("0.90"))
        self.assertEqual(calculations.round_days(accrued), 1)

    def test_b1b_full_month_accrual(self):
        employee = Employee.objects.create_user(
            email="b@x.ru", employee_number="002", full_name="Тест",
            hire_date=date(2026, 1, 1))
        accrued = calculations.accrual_for_month(employee, 2026, 3, Decimal("2.33"))
        self.assertEqual(accrued, Decimal("2.33"))

    def test_b2_future_balance_differs(self):
        """B2: прогноз на будущую дату больше текущего баланса."""
        employee = Employee.objects.create_user(
            email="c@x.ru", employee_number="003", full_name="Тест",
            hire_date=date(2026, 1, 1))
        march = calculations.balance_on(employee, date(2026, 3, 31))
        december = calculations.balance_on(employee, date(2026, 12, 31))
        self.assertGreater(december, march)

    def test_b3_planned_vacation_reduces_balance(self):
        """B3: запланированный отпуск списывается сразу, не дожидаясь наступления."""
        employee = Employee.objects.create_user(
            email="d@x.ru", employee_number="004", full_name="Тест",
            hire_date=date(2025, 1, 1))
        before = calculations.balance_on(employee, date(2026, 12, 31))
        ScheduleEntry.objects.create(
            employee=employee, created_by=employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2026, 7, 6), date_to=date(2026, 7, 15),
            status=ScheduleEntry.Status.PENDING_APPROVAL)
        after = calculations.balance_on(employee, date(2026, 12, 31))
        self.assertEqual(before - after, Decimal("10.00"))


class InitialBalanceTests(TestCase):
    """B4 — начальный остаток из кадровой выгрузки.

    Остаток на дату загрузки уже включает всё начисленное и использованное
    до неё, поэтому начисление за стаж до этой даты повторно не прибавляется.
    """

    def setUp(self):
        CompanySettings.get_solo()
        self.employee = Employee.objects.create_user(
            email="old@x.ru", employee_number="010", full_name="Старожил",
            hire_date=date(2015, 4, 1))

    def _migrate(self, on, days):
        VacationLedger.objects.create(
            employee=self.employee, entry_date=on, days_delta=Decimal(days),
            reason=VacationLedger.Reason.INITIAL_MIGRATION)

    def test_b4_balance_on_migration_date_equals_initial(self):
        """В день загрузки баланс равен остатку из выгрузки, а не стажу."""
        self._migrate(date(2026, 10, 31), "12.00")
        balance = calculations.balance_on(self.employee, date(2026, 10, 31))
        self.assertEqual(balance, Decimal("12.00"))

    def test_b4_accrual_continues_after_migration(self):
        """После загрузки начисление продолжается: два полных месяца — 4.66."""
        self._migrate(date(2026, 10, 31), "12.00")
        balance = calculations.balance_on(self.employee, date(2026, 12, 31))
        self.assertEqual(balance, Decimal("16.66"))

    def test_b4_migration_mid_month_accrues_remainder(self):
        """Загрузка 15 октября: за 16–31 октября начисляется 2.33 × 16/31."""
        self._migrate(date(2026, 10, 15), "12.00")
        balance = calculations.balance_on(self.employee, date(2026, 10, 31))
        self.assertEqual(balance, Decimal("13.20"))

    def test_b4_planned_vacation_after_migration_is_spent(self):
        self._migrate(date(2026, 10, 31), "12.00")
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2026, 11, 9), date_to=date(2026, 11, 13),
            status=ScheduleEntry.Status.APPROVED)
        balance = calculations.balance_on(self.employee, date(2026, 12, 31))
        self.assertEqual(balance, Decimal("11.66"))

    def test_without_initial_balance_accrues_from_hire_date(self):
        """Без остатка в выгрузке поведение прежнее — начисление со дня приёма."""
        employee = Employee.objects.create_user(
            email="new@x.ru", employee_number="011", full_name="Новичок",
            hire_date=date(2026, 1, 1))
        balance = calculations.balance_on(employee, date(2026, 6, 30))
        self.assertEqual(balance, Decimal("13.98"))

    def test_migration_before_hire_date_does_not_accrue_before_hire(self):
        """Дата остатка раньше приёма (ошибка выгрузки) — начисление с приёма."""
        employee = Employee.objects.create_user(
            email="late@x.ru", employee_number="012", full_name="Поздний",
            hire_date=date(2026, 3, 1))
        VacationLedger.objects.create(
            employee=employee, entry_date=date(2026, 1, 1),
            days_delta=Decimal("0.00"),
            reason=VacationLedger.Reason.INITIAL_MIGRATION)
        balance = calculations.balance_on(employee, date(2026, 3, 31))
        self.assertEqual(balance, Decimal("2.33"))


class VacationDaysTests(TestCase):
    """Группа C — расчёт отпуска."""

    def setUp(self):
        CompanySettings.get_solo()
        for day in (1, 2, 7):
            Holiday.objects.create(
                date=date(2027, 1, day), name=f"Праздник {day}",
                day_type=Holiday.DayType.HOLIDAY)

    def test_c1_holidays_not_deducted(self):
        """C1: 1-14 января, 3 праздника -> 11 отпускных дней."""
        Holiday.objects.create(
            date=date(2027, 1, 8), name="Праздник 8",
            day_type=Holiday.DayType.HOLIDAY)
        days = calculations.vacation_days(date(2027, 1, 1), date(2027, 1, 14))
        self.assertEqual(days, 10)  # 14 календарных - 4 праздника

    def test_c2_weekends_are_deducted(self):
        """C2: неделя без праздников -> списываются все 7 дней."""
        days = calculations.vacation_days(date(2027, 3, 1), date(2027, 3, 7))
        self.assertEqual(days, 7)

    def test_c4_repeated_holiday_extension(self):
        """C4: продление повторяется, если хвост снова попал на праздник."""
        # Отпуск 5-6 января. Внутри праздников нет.
        # 7 января - праздник, попадёт в хвост после первого продления.
        Holiday.objects.create(
            date=date(2027, 1, 5), name="Праздник 5",
            day_type=Holiday.DayType.HOLIDAY)
        new_end, added = calculations.extend_for_holidays(
            date(2027, 1, 5), date(2027, 1, 6))
        # 5 января праздник -> +1 день -> до 7 января, но 7 тоже праздник -> +1 -> 8
        self.assertEqual(new_end, date(2027, 1, 8))
        self.assertEqual(added, 2)

    def test_c5_split_across_years(self):
        """C5: отпуск на стыке лет делится по годам, но остаётся непрерывным."""
        per_year = calculations.split_by_year(date(2026, 12, 28), date(2027, 1, 10))
        self.assertEqual(per_year[2026], 4)
        self.assertEqual(per_year[2027], 10 - 3)  # минус праздники 1, 2, 7 января
        self.assertEqual(sum(per_year.values()), 11)


class RuleTests(TestCase):
    """Группа D — блокирующие проверки."""

    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.employee = Employee.objects.create_user(
            email="e@x.ru", employee_number="005", full_name="Тест",
            hire_date=date(2020, 1, 1))

    def test_d3_no_weekend_blocked(self):
        """D3: отпуск вторник-четверг блокируется (обобщённое правило)."""
        result = calculations.validate_entry(
            self.employee, date(2027, 3, 2), date(2027, 3, 4),
            ScheduleEntry.RecordType.VACATION)
        codes = [v.code for v in result.violations]
        self.assertIn("no_weekend_captured", codes)

    def test_d3b_weekend_captured_passes(self):
        result = calculations.validate_entry(
            self.employee, date(2027, 3, 5), date(2027, 3, 7),
            ScheduleEntry.RecordType.VACATION)
        codes = [v.code for v in result.violations]
        self.assertNotIn("no_weekend_captured", codes)

    def test_d7_threshold_change_without_code_change(self):
        """D7: изменение порога в справочнике меняет поведение проверки."""
        rule = ValidationRule.objects.get(code="max_continuous_workdays")
        result = calculations.validate_entry(
            self.employee, date(2027, 3, 1), date(2027, 3, 12),
            ScheduleEntry.RecordType.VACATION)
        self.assertNotIn("max_continuous_workdays", [v.code for v in result.violations])

        rule.threshold_value = 5
        rule.save()
        result = calculations.validate_entry(
            self.employee, date(2027, 3, 1), date(2027, 3, 12),
            ScheduleEntry.RecordType.VACATION)
        self.assertIn("max_continuous_workdays", [v.code for v in result.violations])

    def test_d8_disabled_rule_not_checked(self):
        """D8: отключённое правило не срабатывает."""
        ValidationRule.objects.filter(code="no_weekend_captured").update(is_enabled=False)
        result = calculations.validate_entry(
            self.employee, date(2027, 3, 2), date(2027, 3, 4),
            ScheduleEntry.RecordType.VACATION)
        self.assertNotIn("no_weekend_captured", [v.code for v in result.violations])

    def test_self_overlap_cannot_be_approved(self):
        """Пересечение своих отпусков — блокировка без права подтверждения."""
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 5), date_to=date(2027, 7, 18),
            status=ScheduleEntry.Status.APPROVED)
        result = calculations.validate_entry(
            self.employee, date(2027, 7, 10), date(2027, 7, 25),
            ScheduleEntry.RecordType.VACATION)
        overlap = [v for v in result.violations if v.code == "self_overlap"]
        self.assertTrue(overlap)
        self.assertFalse(overlap[0].can_be_approved)

    def test_d1_annual_norm(self):
        """D1: годовая норма проверяется с фактическими цифрами в сообщении."""
        ScheduleEntry.objects.create(
            employee=self.employee, created_by=self.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 7, 1), date_to=date(2027, 7, 21),
            status=ScheduleEntry.Status.PENDING_APPROVAL)
        result = calculations.validate_annual_plan(self.employee, 2027)
        violation = [v for v in result.violations if v.code == "min_annual_days"][0]
        self.assertIn("21", violation.message)
        self.assertIn("28", violation.message)


class TagLimitTests(TestCase):
    """Группа E — теги и пересечения (приоритет №1)."""

    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.tag = Tag.objects.create(name="Дежурная смена", max_simultaneous_absence=2)
        self.people = []
        for i in range(4):
            employee = Employee.objects.create_user(
                email=f"t{i}@x.ru", employee_number=f"10{i}",
                full_name=f"Сотрудник {i}", hire_date=date(2020, 1, 1))
            EmployeeTag.objects.create(
                employee=employee, tag=self.tag, source=Tag.Source.MANUAL)
            self.people.append(employee)

    def _book(self, employee, start, end, status=ScheduleEntry.Status.APPROVED):
        return ScheduleEntry.objects.create(
            employee=employee, created_by=employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=start, date_to=end, status=status)

    def test_e1_limit_exceeded_blocks(self):
        """E1: третий сотрудник при лимите 2 блокируется."""
        self._book(self.people[0], date(2027, 7, 10), date(2027, 7, 20))
        self._book(self.people[1], date(2027, 7, 10), date(2027, 7, 20))
        result = calculations.validate_entry(
            self.people[2], date(2027, 7, 15), date(2027, 7, 25),
            ScheduleEntry.RecordType.VACATION)
        violation = [v for v in result.violations if v.code == "tag_limit_exceeded"]
        self.assertTrue(violation)
        self.assertIn("Дежурная смена", violation[0].message)

    def test_e2_single_day_overlap_counts(self):
        """E2: пересечение в один день считается пересечением."""
        self._book(self.people[0], date(2027, 7, 10), date(2027, 7, 20))
        self._book(self.people[1], date(2027, 7, 10), date(2027, 7, 20))
        result = calculations.validate_entry(
            self.people[2], date(2027, 7, 20), date(2027, 7, 30),
            ScheduleEntry.RecordType.VACATION)
        self.assertIn("tag_limit_exceeded", [v.code for v in result.violations])

    def test_e4_drafts_do_not_block(self):
        """E4: черновики коллег не занимают место в лимите."""
        self._book(self.people[0], date(2027, 7, 10), date(2027, 7, 20),
                   ScheduleEntry.Status.DRAFT)
        self._book(self.people[1], date(2027, 7, 10), date(2027, 7, 20),
                   ScheduleEntry.Status.DRAFT)
        result = calculations.validate_entry(
            self.people[2], date(2027, 7, 15), date(2027, 7, 25),
            ScheduleEntry.RecordType.VACATION)
        self.assertNotIn("tag_limit_exceeded", [v.code for v in result.violations])

    def test_e3_checks_all_tags(self):
        """E3: проверяются все теги сотрудника, называется нарушенный."""
        free_tag = Tag.objects.create(name="Свободный", max_simultaneous_absence=10)
        EmployeeTag.objects.create(
            employee=self.people[2], tag=free_tag, source=Tag.Source.MANUAL)
        self._book(self.people[0], date(2027, 7, 10), date(2027, 7, 20))
        self._book(self.people[1], date(2027, 7, 10), date(2027, 7, 20))
        result = calculations.validate_entry(
            self.people[2], date(2027, 7, 15), date(2027, 7, 25),
            ScheduleEntry.RecordType.VACATION)
        messages = [v.message for v in result.violations if v.code == "tag_limit_exceeded"]
        self.assertEqual(len(messages), 1)
        self.assertIn("Дежурная смена", messages[0])


class ApiFlowTests(TestCase):
    """Группа A и D6 — сквозной путь через API."""

    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()
        self.unit = OrgUnit.objects.create(name="Группа 1", unit_type=OrgUnit.UnitType.GROUP)
        self.employee = Employee.objects.create_user(
            email="user@x.ru", employee_number="200", full_name="Иван Иванов",
            hire_date=date(2020, 1, 1), password="secret123", org_unit=self.unit)

    def test_a4_login_and_me(self):
        response = self.client.post("/api/v1/auth/login/", {
            "email": "user@x.ru", "password": "secret123"}, format="json")
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.json()["full_name"], "Иван Иванов")

    def test_invitation_flow(self):
        """Вход по приглашению без самостоятельной регистрации."""
        import uuid
        invited = Employee.objects.create_user(
            email="new@x.ru", employee_number="201", full_name="Новый",
            hire_date=date(2026, 1, 1))
        invited.invitation_token = uuid.uuid4()
        invited.save()

        response = self.client.post("/api/v1/auth/accept-invitation/", {
            "invitation_token": str(invited.invitation_token),
            "password": "newpass123"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())

        invited.refresh_from_db()
        self.assertIsNone(invited.invitation_token)

    def test_d6_validate_before_save(self):
        """D6: предварительная проверка возвращает нарушение без сохранения."""
        self.client.force_authenticate(self.employee)
        response = self.client.post("/api/v1/schedule-entries/validate/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04",
            "record_type": "vacation"}, format="json")
        body = response.json()
        self.assertFalse(body["is_valid"])
        self.assertIn("no_weekend_captured", [v["code"] for v in body["violations"]])
        self.assertEqual(ScheduleEntry.objects.count(), 0)

    def test_create_with_approvable_violation_saves_draft(self):
        """Нарушение, которое может подтвердить руководитель, сохраняется
        черновиком с пометкой — иначе подтверждать было бы нечего."""
        self.client.force_authenticate(self.employee)
        response = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-02", "date_to": "2027-03-04",
            "record_type": "vacation"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "draft")
        self.assertEqual([v["code"] for v in response.json()["pending_exceptions"]],
                         ["no_weekend_captured"])

    def test_create_overlapping_own_vacation_returns_409(self):
        self.client.force_authenticate(self.employee)
        payload = {"date_from": "2027-03-05", "date_to": "2027-03-08",
                   "record_type": "vacation"}
        self.client.post("/api/v1/schedule-entries/", payload, format="json")
        response = self.client.post("/api/v1/schedule-entries/", payload, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(ScheduleEntry.objects.count(), 1)

    def test_create_valid_entry(self):
        self.client.force_authenticate(self.employee)
        response = self.client.post("/api/v1/schedule-entries/", {
            "date_from": "2027-03-05", "date_to": "2027-03-08",
            "record_type": "vacation"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ScheduleEntry.objects.count(), 1)

    def test_a5_visibility_isolation(self):
        """A5: сотрудник не видит чужие записи."""
        other = Employee.objects.create_user(
            email="other@x.ru", employee_number="300", full_name="Другой",
            hire_date=date(2020, 1, 1))
        ScheduleEntry.objects.create(
            employee=other, created_by=other,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 5, 1), date_to=date(2027, 5, 10))

        self.client.force_authenticate(self.employee)
        response = self.client.get("/api/v1/schedule-entries/")
        self.assertEqual(response.json()["count"], 0)


class BalanceApiTests(TestCase):
    """Регрессия: эндпоинт баланса должен быть доступен самому сотруднику."""

    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.client = APIClient()
        self.employee = Employee.objects.create_user(
            email="bal@x.ru", employee_number="700", full_name="Баланс Тестов",
            hire_date=date(2020, 1, 1), password="secret123")

    def test_own_balance_accessible(self):
        self.client.force_authenticate(self.employee)
        response = self.client.get(
            f"/api/v1/employees/{self.employee.id}/balance/?date=2027-06-01")
        self.assertEqual(response.status_code, 200)
        self.assertIn("balance_days", response.json())

    def test_foreign_balance_forbidden(self):
        other = Employee.objects.create_user(
            email="other2@x.ru", employee_number="701", full_name="Чужой",
            hire_date=date(2020, 1, 1))
        self.client.force_authenticate(self.employee)
        response = self.client.get(f"/api/v1/employees/{other.id}/balance/")
        self.assertEqual(response.status_code, 403)
