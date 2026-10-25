"""Демонстрационные данные для проверки сквозного сценария."""

import uuid
from datetime import date

from django.core.management.base import BaseCommand

from config.commands import WorkingDatabaseChecks
from company.models import AbsenceReason, CompanySettings, Holiday
from employees.models import Employee, EmployeeTag, OrgUnit, Tag
from schedule.models import ValidationRule

RULES = [
    ("min_annual_days", "Годовая норма отпуска", 28,
     "За календарный год должно быть запланировано не менее нормы дней"),
    ("min_continuous_days", "Непрерывный отпуск", 14,
     "Хотя бы один отпуск должен длиться не менее указанного числа дней"),
    ("max_continuous_workdays", "Длительное отсутствие", 11,
     "Непрерывное отсутствие (отпуска, между которыми только выходные и праздники) "
     "больше указанного числа рабочих дней требует подтверждения руководителя"),
    ("annual_ceiling", "Годовой потолок отпуска", 42,
     "За год нельзя запланировать больше, чем норма плюс накопленное на 1 января, "
     "и больше порога; превышение требует подтверждения руководителя"),
    ("balance_overrun", "Превышение накопленного баланса", 7,
     "Превышение над накопленными днями более порога требует подтверждения"),
    ("no_weekend_captured", "Отпуск без захвата выходных", None,
     "Период должен захватывать хотя бы один выходной день"),
    ("tag_limit_exceeded", "Лимит одновременного отсутствия по тегу", None,
     "Число сотрудников с одним тегом в отпуске не должно превышать лимит"),
]

# Используется и демо-режимом (demo/loader.py): справочники должны совпадать.
ABSENCE_REASONS = [
    ("sick_leave", "Больничный", False),
    ("unpaid_leave", "Отпуск за свой счёт", False),
    ("family_reasons", "Семейные обстоятельства", False),
    ("public_duty", "Государственные обязанности", False),
    ("absence_no_reason", "Отсутствие без уважительной причины", False),
    ("other", "Другое", True),
]

HOLIDAYS_2027 = [
    (date(2027, 1, 1), "Новый год"),
    (date(2027, 1, 4), "Новогодние каникулы"),
    (date(2027, 1, 5), "Новогодние каникулы"),
    (date(2027, 1, 6), "Новогодние каникулы"),
    (date(2027, 1, 7), "Рождество"),
    (date(2027, 2, 23), "День защитника Отечества"),
    (date(2027, 3, 8), "Международный женский день"),
    (date(2027, 5, 1), "Праздник Весны и Труда"),
    (date(2027, 5, 9), "День Победы"),
    (date(2027, 6, 12), "День России"),
    (date(2027, 11, 4), "День народного единства"),
]


class Command(WorkingDatabaseChecks, BaseCommand):
    help = "Наполняет базу демонстрационными данными"

    def handle(self, *args, **options):
        settings_row = CompanySettings.get_solo()
        settings_row.organization_name = "Демо-организация"
        settings_row.save()

        for code, name, threshold, description in RULES:
            ValidationRule.objects.update_or_create(
                code=code,
                defaults={"name": name, "threshold_value": threshold,
                          "description": description},
            )

        for day, name in HOLIDAYS_2027:
            Holiday.objects.get_or_create(
                date=day, defaults={"name": name,
                                    "day_type": Holiday.DayType.HOLIDAY})

        for code, name, is_other in ABSENCE_REASONS:
            AbsenceReason.objects.get_or_create(
                code=code, defaults={"name": name, "is_other": is_other})

        division = OrgUnit.objects.get_or_create(
            name="Дивизион ИТ", unit_type=OrgUnit.UnitType.DIVISION)[0]
        department = OrgUnit.objects.get_or_create(
            name="Отдел инфраструктуры", unit_type=OrgUnit.UnitType.DEPARTMENT,
            parent=division)[0]
        group = OrgUnit.objects.get_or_create(
            name="Группа эксплуатации", unit_type=OrgUnit.UnitType.GROUP,
            parent=department)[0]

        manager = self._employee("500", "Пётр Руководителев", "manager@demo.ru",
                                 date(2018, 3, 1), department, password="demo12345")
        group.manager = manager
        group.save()
        department.manager = manager
        department.save()

        admin = self._employee("501", "Админ Админов", "admin@demo.ru",
                               date(2019, 1, 15), department, password="demo12345")
        admin.is_admin = True
        admin.is_staff = True
        admin.is_superuser = True
        admin.save()

        duty_tag = Tag.objects.get_or_create(
            name="Дежурная смена", defaults={"max_simultaneous_absence": 2})[0]
        group_tag = Tag.objects.get_or_create(
            name="Группа эксплуатации", defaults={"max_simultaneous_absence": 3})[0]

        for index, full_name in enumerate([
            "Иван Иванов", "Мария Петрова", "Сергей Сидоров", "Анна Кузнецова",
        ], start=1):
            employee = self._employee(
                f"60{index}", full_name, f"user{index}@demo.ru",
                date(2021, 1, 1), group,
                password="demo12345" if index == 1 else None)
            EmployeeTag.objects.get_or_create(
                employee=employee, tag=group_tag,
                defaults={"source": Tag.Source.AUTO_ORG})
            if index <= 3:
                EmployeeTag.objects.get_or_create(
                    employee=employee, tag=duty_tag,
                    defaults={"source": Tag.Source.MANUAL})

        self.stdout.write(self.style.SUCCESS(
            "Демо-данные загружены.\n"
            "  Сотрудник:     user1@demo.ru / demo12345\n"
            "  Руководитель:  manager@demo.ru / demo12345\n"
            "  Администратор: admin@demo.ru / demo12345\n"
            "  Остальные сотрудники — с приглашениями (пароль не задан)"))

    def _employee(self, number, full_name, email, hire_date, org_unit, password=None):
        employee = Employee.objects.filter(employee_number=number).first()
        if employee:
            return employee
        employee = Employee.objects.create_user(
            email=email, employee_number=number, full_name=full_name,
            hire_date=hire_date, org_unit=org_unit, password=password)
        if password is None:
            employee.invitation_token = uuid.uuid4()
            employee.save(update_fields=["invitation_token"])
        return employee
