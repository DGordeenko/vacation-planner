"""Загрузка демонстрационных данных в демо-базу.

Данные создаются теми же функциями, что работают в рабочей системе:
импорт кадровой выгрузки, проверка записи, отправка графика, решение
по шагу согласования. Поэтому демо показывает настоящее поведение
системы, а не нарисованную картинку, и не расходится с ней после правок.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.core.management import call_command
from django.db import transaction

from audit.context import acting_as
from company.models import AbsenceReason, CompanySettings, Holiday
from demo import data
from demo.db import DEMO_ALIAS, demo_enabled, same_database_as_default, use_demo_database
from employees.importers import import_employees
from employees.models import Employee, Tag
from schedule import approvals, calculations
from schedule.management.commands.seed_demo import ABSENCE_REASONS, RULES
from schedule.models import ScheduleApproval, ScheduleEntry, ValidationRule


class DemoLoadError(Exception):
    pass


@dataclass
class LoadReport:
    employees: int = 0
    entries: int = 0
    approved: int = 0
    pending: int = 0
    drafts: int = 0
    skipped_periods: list = field(default_factory=list)


def load(stdout=None) -> LoadReport:
    """Очистить демо-базу и наполнить её заново."""
    if not demo_enabled():
        raise DemoLoadError(
            "Демо-режим выключен. Включается явно: DEMO_MODE_ENABLED=True.")
    if same_database_as_default():
        raise DemoLoadError(
            "DEMO_DATABASE_URL указывает на рабочую базу. Загрузка демо-данных "
            "стёрла бы рабочие — задайте для демо отдельную базу.")

    # flush вызывается с явным псевдонимом демо-базы и до подмены соединения:
    # очистка — единственная операция, которую нельзя направить не туда.
    call_command("flush", database=DEMO_ALIAS, interactive=False, verbosity=0)

    # Наполнение — одной транзакцией: при сбое демо-база остаётся пустой,
    # а не наполовину заполненной. С пустой базой кнопка демо на экране
    # входа просто не показывается, а не ведёт в сломанную компанию.
    with use_demo_database(), transaction.atomic():
        with acting_as(None, source="demo"):
            return _fill(stdout)


def _fill(stdout) -> LoadReport:
    report = LoadReport()
    _reference_data()

    import_report = import_employees(data.STAFF_CSV.read_bytes())
    if not import_report.applied:
        raise DemoLoadError(
            "Демонстрационная выгрузка не загрузилась: "
            + "; ".join(str(p) for p in import_report.problems[:5]))
    report.employees = Employee.objects.count()

    for name, limit in data.TAG_LIMITS.items():
        Tag.objects.filter(name=name).update(max_simultaneous_absence=limit)

    admin = Employee.objects.get(
        employee_number=data.ROLES_BY_CODE["admin"].employee_number)
    admin.is_admin = True
    admin.save(update_fields=["is_admin"])

    _plan_team(report)
    _plan_everyone_else(report)
    _time_tracking_examples(admin)

    if stdout:
        stdout.write(
            f"Демо-данные загружены. Сотрудников: {report.employees}, "
            f"отпусков: {report.entries}. Графики: утверждено — {report.approved}, "
            f"на согласовании — {report.pending}, черновики — {report.drafts}.")
    return report


def _reference_data():
    settings_row = CompanySettings.get_solo()
    settings_row.organization_name = data.ORGANIZATION_NAME
    settings_row.save()

    for code, name, threshold, description in RULES:
        ValidationRule.objects.update_or_create(
            code=code, defaults={"name": name, "threshold_value": threshold,
                                 "description": description})

    for day, name in data.HOLIDAYS:
        Holiday.objects.update_or_create(
            date=day, defaults={"name": name, "day_type": Holiday.DayType.HOLIDAY})
    for day, name, day_type in data.SPECIAL_DAYS:
        Holiday.objects.update_or_create(
            date=day, defaults={"name": name, "day_type": day_type})

    for code, name, is_other in ABSENCE_REASONS:
        AbsenceReason.objects.update_or_create(
            code=code, defaults={"name": name, "is_other": is_other})


# --------------------------------------------------------------------------
# Графики отпусков
# --------------------------------------------------------------------------

def _try_add(employee, start, days, report) -> bool:
    """Внести отпуск, если он проходит все проверки — как при сохранении в API."""
    end = start + timedelta(days=days - 1)
    result = calculations.validate_entry(
        employee, start, end, ScheduleEntry.RecordType.VACATION)
    if not result.is_valid:
        return False
    ScheduleEntry.objects.create(
        employee=employee, created_by=employee,
        record_type=ScheduleEntry.RecordType.VACATION,
        date_from=start, date_to=end)
    report.entries += 1
    return True


def _finish(employee, target, report, rng=None):
    """Довести график до нужного статуса через штатное согласование."""
    if target == "draft":
        report.drafts += 1
        return
    try:
        approval = approvals.submit_year(employee, data.PLAN_YEAR, employee)
    except approvals.ApprovalError:
        # Не прошёл годовые проверки или некому согласовать — остаётся
        # черновиком, как и у живого сотрудника.
        report.drafts += 1
        return

    steps = list(approval.steps.order_by("order"))
    if target == "approved":
        decided = len(steps)
    else:
        # На согласовании: часть уровней уже дала согласие.
        decided = rng.randrange(len(steps)) if rng and len(steps) > 1 else 0
    for step in steps[:decided]:
        approvals.decide_step(step, step.approver, True)

    approval.refresh_from_db()
    if approval.status == ScheduleApproval.Status.APPROVED:
        report.approved += 1
    else:
        report.pending += 1


def _plan_team(report):
    for number, (target, blocks) in data.TEAM_PLANS.items():
        employee = Employee.objects.get(employee_number=number)
        for (month, day), days in blocks:
            start = date(data.PLAN_YEAR, month, day)
            if not _try_add(employee, start, days, report):
                report.skipped_periods.append((number, start, days))
        _finish(employee, target, report)


def _free_weeks(holidays):
    """Понедельники года, с которых неделя не задевает праздники."""
    monday = date(data.PLAN_YEAR, 2, 1)
    while monday.weekday() != 0:
        monday += timedelta(days=1)
    weeks = []
    while monday.year == data.PLAN_YEAR and monday.month <= 11:
        week = {monday + timedelta(days=i) for i in range(7)}
        if not week & holidays:
            weeks.append(monday)
        monday += timedelta(days=7)
    return weeks


def _plan_everyone_else(report):
    rng = random.Random(data.RANDOM_SEED)
    holidays = set(Holiday.objects.filter(
        day_type=Holiday.DayType.HOLIDAY).values_list("date", flat=True))
    weeks = _free_weeks(holidays)
    two_weeks = [w for w in weeks if w + timedelta(days=7) in weeks]
    summer = [w for w in two_weeks if 6 <= w.month <= 8]

    # Директору тоже подбирается график, но отправить его некому: у вершины
    # структуры нет руководителя, а других администраторов нет. Он останется
    # черновиком — так же, как в рабочей системе без второго администратора.
    skip = set(data.TEAM_PLANS)
    statuses = [code for code, share in data.STATUS_SHARES for _ in range(share)]

    for employee in Employee.objects.exclude(employee_number__in=skip).order_by(
            "employee_number"):
        taken = []
        # Основной отпуск на две недели — чаще летом, как в жизни.
        pool = summer if rng.random() < 0.7 else two_weeks
        for start in rng.sample(pool, len(pool)):
            if _try_add(employee, start, 14, report):
                taken.append((start, start + timedelta(days=13)))
                break
        # Ещё две недели по семь дней, не вплотную к уже взятым.
        for start in rng.sample(weeks, len(weeks)):
            if len(taken) == 3:
                break
            end = start + timedelta(days=6)
            if any(start <= t_end + timedelta(days=14) and end >= t_start - timedelta(days=14)
                   for t_start, t_end in taken):
                continue
            if _try_add(employee, start, 7, report):
                taken.append((start, end))
        _finish(employee, rng.choice(statuses), report, rng)


def _time_tracking_examples(admin):
    """РВД с отгулом и больничный — чтобы разделы учёта времени не были пустыми."""
    employee = Employee.objects.get(
        employee_number=data.ROLES_BY_CODE["employee"].employee_number)
    rvd = ScheduleEntry.objects.create(
        employee=employee, created_by=admin,
        record_type=ScheduleEntry.RecordType.RVD,
        status=ScheduleEntry.Status.APPROVED,
        date_from=date(2026, 9, 12), work_hours=8,
        rvd_compensation_type=ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF,
        task_link="https://jira.test-corp.example/browse/MON-412")
    calculations.sync_rvd_accrual(rvd)

    sick = AbsenceReason.objects.get(code="sick_leave")
    ScheduleEntry.objects.create(
        employee=employee, created_by=employee,
        record_type=ScheduleEntry.RecordType.ABSENCE,
        status=ScheduleEntry.Status.APPROVED,
        date_from=date(2026, 3, 16), date_to=date(2026, 3, 18), reason=sick)

