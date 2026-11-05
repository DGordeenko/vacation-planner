"""Сверхурочная работа: проверки, согласование, начисление отгула, отчёт.

Порядок, принятый в компании (ноябрь 2026):
- вносит сам сотрудник или его руководитель (администратор — за любого);
- согласуют два уровня руководства вверх от сотрудника; если руководителей
  меньше, цепочка короче, без руководителей — администратор
  (`approvals.build_chain`);
- компенсация — «оплата по законодательству» (уходит в отчёт для расчёта)
  или «отгул» (часы один к одному в регистр отгулов после согласования);
- пределы ТК (4 часа за два дня подряд, 120 часов в год) проверяются, только
  если администратор включил контроль, и дают предупреждение, а не запрет.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction

from company.models import CompanySettings, Holiday
from schedule import approvals
from schedule.models import Notification, ScheduleEntry, TimeOffLedger

APPROVAL_LEVELS = 2
LIVE_STATUSES = [ScheduleEntry.Status.PENDING_APPROVAL, ScheduleEntry.Status.APPROVED]


class OvertimeError(Exception):
    """Запись нельзя сохранить или решение нельзя принять."""


@dataclass
class Check:
    hours: Decimal = Decimal("0")
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def period_hours(time_from, time_to) -> Decimal:
    """Длина периода в часах. Через полночь период не переходит."""
    start = datetime.combine(date.min, time_from)
    end = datetime.combine(date.min, time_to)
    return Decimal((end - start).seconds) / Decimal(3600)


def day_type(day: date) -> str:
    """Тип дня для отчёта: от него зависит оплата."""
    special = Holiday.objects.filter(date=day).first()
    if special is not None:
        return {
            Holiday.DayType.HOLIDAY: "праздничный",
            Holiday.DayType.PRE_HOLIDAY_SHORT: "предпраздничный",
            Holiday.DayType.TRANSFERRED_WORKDAY: "рабочий (перенесённый)",
        }[special.day_type]
    return "выходной" if day.weekday() >= 5 else "рабочий"


def _hours_on(employee, days, exclude_id=None):
    rows = ScheduleEntry.objects.filter(
        employee=employee, record_type=ScheduleEntry.RecordType.OVERTIME,
        date_from__in=days, status__in=LIVE_STATUSES)
    if exclude_id:
        rows = rows.exclude(id=exclude_id)
    return sum((row.overtime_hours or Decimal("0") for row in rows), Decimal("0"))


def check(employee, day, time_from, time_to, exclude_id=None) -> Check:
    """Ошибки (запись не сохраняется) и предупреждения о пределах."""
    result = Check()
    if day > date.today():
        result.errors.append("Сверхурочные вносятся по факту — дата ещё не наступила")
    if time_to <= time_from:
        result.errors.append("Окончание должно быть позже начала в тот же день")
        return result
    result.hours = period_hours(time_from, time_to)

    same_day = ScheduleEntry.objects.filter(
        employee=employee, record_type=ScheduleEntry.RecordType.OVERTIME,
        date_from=day, status__in=LIVE_STATUSES)
    if exclude_id:
        same_day = same_day.exclude(id=exclude_id)
    for other in same_day:
        if other.work_time_from < time_to and time_from < other.work_time_to:
            result.errors.append(
                f"Пересекается с уже внесённым периодом "
                f"{other.work_time_from:%H:%M}–{other.work_time_to:%H:%M}")

    settings_row = CompanySettings.get_solo()
    if settings_row.overtime_limits_enabled:
        two_day_limit = settings_row.overtime_limit_two_days_hours
        for pair in ((day - timedelta(days=1), day), (day, day + timedelta(days=1))):
            total = _hours_on(employee, pair, exclude_id) + result.hours
            if total > two_day_limit:
                result.warnings.append(
                    f"За {pair[0]:%d.%m} и {pair[1]:%d.%m} получится {_fmt(total)} ч "
                    f"сверхурочных — больше {_fmt(two_day_limit)} ч за два дня подряд")
                break
        year_total = sum(
            (row.overtime_hours or Decimal("0") for row in ScheduleEntry.objects.filter(
                employee=employee, record_type=ScheduleEntry.RecordType.OVERTIME,
                date_from__year=day.year, status__in=LIVE_STATUSES,
            ).exclude(id=exclude_id)), Decimal("0")) + result.hours
        year_limit = settings_row.overtime_limit_year_hours
        if year_total > year_limit:
            result.warnings.append(
                f"За {day.year} год получится {_fmt(year_total)} ч сверхурочных — "
                f"больше {_fmt(year_limit)} ч")
    return result


def _fmt(hours: Decimal) -> str:
    return f"{hours.normalize():f}".replace(".", ",")


def year_hours(employee, year) -> Decimal:
    """Сумма согласованных и ждущих согласования часов за год."""
    rows = ScheduleEntry.objects.filter(
        employee=employee, record_type=ScheduleEntry.RecordType.OVERTIME,
        date_from__year=year, status__in=LIVE_STATUSES)
    return sum((row.overtime_hours or Decimal("0") for row in rows), Decimal("0"))


def can_record_for(actor, employee) -> bool:
    if actor.is_admin or actor.id == employee.id:
        return True
    return employee.id in actor.structure_employee_ids()


def chain(entry):
    return [person for person, _level in approvals.build_chain(entry.employee,
                                                               APPROVAL_LEVELS)]


def current_approver(entry):
    """Кто решает сейчас; None — решение принято или решать некому."""
    if entry.status != ScheduleEntry.Status.PENDING_APPROVAL:
        return None
    people = chain(entry)
    return people[entry.approval_stage] if entry.approval_stage < len(people) else None


def _advance(entry, actor):
    """Пропускает шаги, которые уже согласовал сам автор или текущий решающий.

    Руководитель, внёсший сверхурочные подчинённого, этим их и согласовал —
    просить его визировать собственную запись бессмысленно.
    """
    people = chain(entry)
    while entry.approval_stage < len(people) and people[entry.approval_stage].id == actor.id:
        entry.approval_stage += 1
    if entry.approval_stage >= len(people):
        entry.status = ScheduleEntry.Status.APPROVED


@transaction.atomic
def create(employee, actor, day, time_from, time_to, task_link, compensation):
    result = check(employee, day, time_from, time_to)
    if result.errors:
        raise OvertimeError("; ".join(result.errors))
    entry = ScheduleEntry(
        employee=employee, created_by=actor,
        record_type=ScheduleEntry.RecordType.OVERTIME,
        date_from=day, date_to=day, work_time_from=time_from, work_time_to=time_to,
        overtime_hours=result.hours, task_link=task_link,
        overtime_compensation=compensation,
        status=ScheduleEntry.Status.PENDING_APPROVAL)
    if actor.is_admin and actor.id != employee.id:
        # Запись администратора и есть подтверждение — как у РВД.
        entry.status = ScheduleEntry.Status.APPROVED
    else:
        _advance(entry, actor)
    entry.save()
    _after_decision(entry)
    return entry, result.warnings


@transaction.atomic
def decide(entry, actor, approved, comment=""):
    if entry.status != ScheduleEntry.Status.PENDING_APPROVAL:
        raise OvertimeError("Решение по этой записи уже принято")
    approver = current_approver(entry)
    if not (actor.is_admin or (approver and approver.id == actor.id)):
        raise PermissionError("Сейчас решение за другим руководителем")
    if not approved:
        comment = (comment or "").strip()
        if not comment:
            raise OvertimeError("Комментарий обязателен при отклонении")
        entry.status = ScheduleEntry.Status.REJECTED
        entry.rejection_comment = comment
        entry.save(update_fields=["status", "rejection_comment"])
        approvals.notify(entry.employee, Notification.NotificationType.APPROVAL_DECIDED,
                         f"Сверхурочные {entry.date_from:%d.%m} отклонены: {comment}", entry)
        return entry
    entry.approval_stage += 1
    _advance(entry, actor)
    entry.save(update_fields=["status", "approval_stage"])
    _after_decision(entry)
    return entry


def _after_decision(entry):
    """Начисление отгула и уведомления после сохранения записи."""
    if entry.status == ScheduleEntry.Status.APPROVED:
        if entry.overtime_compensation == ScheduleEntry.OvertimeCompensation.DAY_OFF:
            TimeOffLedger.objects.get_or_create(
                related_schedule_entry=entry, reason=TimeOffLedger.Reason.OVERTIME_EARNED,
                defaults={"employee": entry.employee, "entry_date": entry.date_from,
                          "hours_delta": entry.overtime_hours,
                          "comment": f"Сверхурочные {entry.date_from}"})
        if entry.created_by_id != entry.employee_id or entry.approval_stage:
            approvals.notify(entry.employee, Notification.NotificationType.APPROVAL_DECIDED,
                             f"Сверхурочные {entry.date_from:%d.%m} согласованы", entry)
        return
    approver = current_approver(entry)
    if approver:
        approvals.notify(approver, Notification.NotificationType.APPROVAL_REQUIRED,
                         f"{entry.employee.full_name}: сверхурочные "
                         f"{entry.date_from:%d.%m}, {_fmt(entry.overtime_hours)} ч", entry)


def awaiting(user):
    """Сверхурочные, где очередь решения дошла до пользователя."""
    pending = ScheduleEntry.objects.filter(
        record_type=ScheduleEntry.RecordType.OVERTIME,
        status=ScheduleEntry.Status.PENDING_APPROVAL,
    ).select_related("employee", "employee__org_unit").order_by("date_from")
    return [entry for entry in pending
            if (approver := current_approver(entry)) and approver.id == user.id]


def report_rows(entries):
    """Строки отчёта к оплате: только поля, нужные для расчёта."""
    return [{
        "full_name": e.employee.full_name,
        "employee_number": e.employee.employee_number,
        "date": e.date_from,
        "day_type": day_type(e.date_from),
        "period": f"{e.work_time_from:%H:%M}–{e.work_time_to:%H:%M}",
    } for e in entries]

