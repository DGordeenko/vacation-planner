"""Алгоритмы расчётов. Единственная реализация на всю систему.

Соответствует документу «05-CALCULATIONS.md». Все проверки — и при ручном
вводе, и при загрузке CSV, и при предварительной проверке — идут сюда.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from company.models import CompanySettings, Holiday
from employees.models import Employee
from schedule.models import ScheduleEntry, ValidationRule, VacationLedger

SATURDAY, SUNDAY = 5, 6


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_end(year: int, month: int) -> date:
    return date(year, month, _days_in_month(year, month))


def round_days(value: Decimal) -> int:
    """Математическое округление — только для отображения и сравнения."""
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# Накопление отпускных дней
# --------------------------------------------------------------------------

def accrual_start(employee: Employee) -> date:
    """С какого дня начисляются отпускные дни.

    Если при загрузке кадровой выгрузки задан начальный остаток, начисление
    идёт со дня, следующего за датой остатка: остаток уже включает всё,
    что было начислено и использовано до него. Иначе — с даты приёма.
    Без этого правила остаток прибавлялся к начислению за весь стаж,
    и у сотрудника с 2008 года выходило больше 500 дней.
    """
    migration = (VacationLedger.objects
                 .filter(employee=employee,
                         reason=VacationLedger.Reason.INITIAL_MIGRATION)
                 .order_by("-entry_date").first())
    if migration is None:
        return employee.hire_date
    return max(employee.hire_date, migration.entry_date + timedelta(days=1))


def accrual_for_month(employee: Employee, year: int, month: int, rate: Decimal,
                      start_date: date = None) -> Decimal:
    """Начисление за месяц. За неполный месяц — пропорционально дням в штате."""
    total_days = _days_in_month(year, month)
    period_start = date(year, month, 1)
    period_end = date(year, month, total_days)

    start = max(period_start, start_date or employee.hire_date)
    end = period_end
    if employee.termination_date and employee.termination_date < end:
        end = employee.termination_date

    if start > end:
        return Decimal("0.00")

    days_employed = (end - start).days + 1
    if days_employed >= total_days:
        return rate.quantize(Decimal("0.01"))

    portion = rate * Decimal(days_employed) / Decimal(total_days)
    return portion.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def accrued_total(employee: Employee, until: date, rate: Decimal,
                  start_date: date = None) -> Decimal:
    """Сумма начислений с даты начала начисления по указанную дату включительно.

    Дата начала — дата приёма либо день после начального остатка
    (см. accrual_start). Месяц засчитывается, когда он завершился
    (или когда `until` внутри него — тогда пропорционально прожитой части).
    """
    start_date = start_date or employee.hire_date
    total = Decimal("0.00")
    year, month = start_date.year, start_date.month

    while date(year, month, 1) <= until:
        month_end = _month_end(year, month)
        if month_end <= until:
            total += accrual_for_month(employee, year, month, rate, start_date)
        else:
            # Незавершённый месяц — считаем пропорционально до `until`.
            start = max(date(year, month, 1), start_date)
            if until >= start:
                days = (until - start).days + 1
                total += (rate * Decimal(days) / Decimal(_days_in_month(year, month))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
        month = month + 1 if month < 12 else 1
        year = year if month != 1 else year + 1

    return total.quantize(Decimal("0.01"))


def balance_on(employee: Employee, on_date: date) -> Decimal:
    """Баланс отпускных дней на произвольную дату.

    Учитываются и запланированные отпуска — иначе один и тот же остаток
    можно было бы запланировать дважды.
    """
    settings_row = CompanySettings.get_solo()
    rate = settings_row.monthly_accrual_rate

    accrued = accrued_total(employee, on_date, rate, accrual_start(employee))

    manual = VacationLedger.objects.filter(
        employee=employee, entry_date__lte=on_date
    ).exclude(reason=VacationLedger.Reason.MONTHLY_ACCRUAL)
    manual_total = sum((row.days_delta for row in manual), Decimal("0.00"))

    planned = ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.VACATION,
        date_from__lte=on_date,
        status__in=[
            ScheduleEntry.Status.PENDING_APPROVAL,
            ScheduleEntry.Status.APPROVED,
        ],
    )
    spent = sum((Decimal(vacation_days(e.date_from, e.effective_date_to)) for e in planned), Decimal("0.00"))

    return (accrued + manual_total - spent).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------
# Длительность отпуска и праздники
# --------------------------------------------------------------------------

def holiday_dates(date_from: date, date_to: date) -> set:
    """Праздничные нерабочие дни внутри периода."""
    return set(
        Holiday.objects.filter(
            date__gte=date_from, date__lte=date_to, day_type=Holiday.DayType.HOLIDAY
        ).values_list("date", flat=True)
    )


def vacation_days(date_from: date, date_to: date) -> int:
    """Отпускные дни = календарные минус праздничные.

    Выходные входят в отпускные дни и списываются с баланса.
    """
    calendar_days = (date_to - date_from).days + 1
    return calendar_days - len(holiday_dates(date_from, date_to))


def captures_weekend(date_from: date, date_to: date) -> bool:
    current = date_from
    while current <= date_to:
        if current.weekday() in (SATURDAY, SUNDAY):
            return True
        current += timedelta(days=1)
    return False


def extend_for_holidays(date_from: date, date_to: date):
    """Продление отпуска на праздники.

    Итеративно: если добавленный хвост снова попал на праздник,
    продление выполняется повторно.
    """
    current_end = date_to
    added = 0
    while True:
        tail_start = current_end + timedelta(days=1)
        holidays_inside = holiday_dates(date_from, current_end)
        needed = len(holidays_inside) - added
        if needed <= 0:
            break
        new_end = current_end + timedelta(days=needed)
        added += needed
        current_end = new_end
        # Хвост проверяется на следующей итерации через holiday_dates.
        if not holiday_dates(tail_start, current_end):
            break
    return current_end, added


def split_by_year(date_from: date, date_to: date) -> dict:
    """Отпуск на стыке лет: дни распределяются по календарным годам."""
    holidays = holiday_dates(date_from, date_to)
    per_year: dict = {}
    current = date_from
    while current <= date_to:
        if current not in holidays:
            per_year[current.year] = per_year.get(current.year, 0) + 1
        current += timedelta(days=1)
    return per_year


# --------------------------------------------------------------------------
# Правила проверки
# --------------------------------------------------------------------------

@dataclass
class RuleViolation:
    code: str
    name: str
    message: str
    can_be_approved: bool = True
    # Для лимита по тегу: кто из коллег уже в отпуске и когда. Сотрудникам
    # проще договориться о датах, когда видно, с кем пересечение.
    colleagues: list = field(default_factory=list)


@dataclass
class ValidationResult:
    violations: list = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.violations


def _rule(code: str):
    return ValidationRule.objects.filter(code=code, is_enabled=True).first()


def working_dates(date_from: date, date_to: date) -> set:
    """Рабочие дни периода: будни без праздников плюс перенесённые рабочие дни."""
    days = Holiday.objects.filter(date__gte=date_from, date__lte=date_to)
    holidays = {d.date for d in days if d.day_type == Holiday.DayType.HOLIDAY}
    transferred = {d.date for d in days
                   if d.day_type == Holiday.DayType.TRANSFERRED_WORKDAY}
    result = set()
    current = date_from
    while current <= date_to:
        if current in transferred or (
                current.weekday() not in (SATURDAY, SUNDAY) and current not in holidays):
            result.add(current)
        current += timedelta(days=1)
    return result


def _active_vacations(employee, exclude_entry_id=None):
    entries = ScheduleEntry.objects.filter(
        employee=employee, record_type=ScheduleEntry.RecordType.VACATION,
    ).exclude(status__in=[ScheduleEntry.Status.CANCELLED, ScheduleEntry.Status.REJECTED])
    if exclude_entry_id:
        entries = entries.exclude(id=exclude_entry_id)
    return entries


def continuous_block(employee, date_from: date, date_to: date, exclude_entry_id=None):
    """Непрерывное отсутствие, в которое входит период.

    Отпуска, между которыми только выходные и праздники, — одно отсутствие:
    пять дней, выходные, ещё пять дней, выходные и три дня — это 13 рабочих
    дней подряд, сколько бы записей их ни составляло.
    """
    others = [(e.date_from, e.effective_date_to) for e in _active_vacations(
        employee, exclude_entry_id)]
    start, end = date_from, date_to
    changed = True
    while changed:
        changed = False
        for other_from, other_to in others:
            if other_from >= start and other_to <= end:
                continue
            touches_before = other_to < start and not working_dates(
                other_to + timedelta(days=1), start - timedelta(days=1))
            touches_after = other_from > end and not working_dates(
                end + timedelta(days=1), other_from - timedelta(days=1))
            overlaps = other_from <= end and other_to >= start
            if touches_before or touches_after or overlaps:
                start, end = min(start, other_from), max(end, other_to)
                changed = True
    return start, end


def planned_days_in_year(employee, year: int, exclude_entry_id=None) -> int:
    """Отпускные дни года по всем отпускам сотрудника, включая черновики."""
    return sum(split_by_year(e.date_from, e.effective_date_to).get(year, 0)
               for e in _active_vacations(employee, exclude_entry_id))


def annual_ceiling(employee, year: int):
    """Сколько дней можно запланировать на год.

    Норма плюс накопленное на 1 января, но не больше порога правила
    annual_ceiling (42 по умолчанию). Возвращает потолок и значение
    «норма + накопленное» — оно нужно для объяснения в сообщении.
    """
    settings_row = CompanySettings.get_solo()
    norm = int(employee.annual_vacation_norm_override or settings_row.annual_vacation_norm_days)
    stock_raw = balance_on(employee, date(year, 1, 1))
    stock = int(stock_raw) if stock_raw > 0 else 0
    accumulated_ceiling = norm + stock
    rule = _rule("annual_ceiling")
    if rule and rule.threshold_value is not None:
        return min(accumulated_ceiling, int(rule.threshold_value)), accumulated_ceiling
    return accumulated_ceiling, accumulated_ceiling


def own_draft_days(employee, on_date: date, exclude_entry_id=None) -> Decimal:
    """Отпускные дни собственных черновиков, начинающихся не позже даты."""
    drafts = _active_vacations(employee, exclude_entry_id).filter(
        status=ScheduleEntry.Status.DRAFT, date_from__lte=on_date)
    return sum((Decimal(vacation_days(e.date_from, e.effective_date_to)) for e in drafts),
               Decimal("0"))


def validate_entry(employee: Employee, date_from: date, date_to: date, record_type: str,
                   exclude_entry_id=None) -> ValidationResult:
    """Проверки уровня отдельной записи."""
    result = ValidationResult()
    if record_type != ScheduleEntry.RecordType.VACATION:
        return result

    days = vacation_days(date_from, date_to)

    # Пересечение с собственным отпуском — не логическое правило,
    # а физически невозможная ситуация: подтверждению не подлежит.
    overlapping = ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.VACATION,
        date_from__lte=date_to,
    ).exclude(status=ScheduleEntry.Status.CANCELLED)
    if exclude_entry_id:
        overlapping = overlapping.exclude(id=exclude_entry_id)
    for entry in overlapping:
        if entry.effective_date_to >= date_from:
            result.violations.append(
                RuleViolation(
                    code="self_overlap",
                    name="Пересечение отпусков",
                    message=f"Период пересекается с уже внесённым отпуском "
                            f"{entry.date_from}—{entry.effective_date_to}",
                    can_be_approved=False,
                )
            )
            break

    rule = _rule("max_continuous_workdays")
    if rule:
        start, end = continuous_block(employee, date_from, date_to, exclude_entry_id)
        workdays = len(working_dates(start, end))
        if workdays > rule.threshold_value:
            joined = (start, end) != (date_from, date_to)
            result.violations.append(
                RuleViolation(
                    code=rule.code,
                    name=rule.name,
                    message=(f"{'Вместе с соседними отпусками непрерывное' if joined else 'Непрерывное'}"
                             f" отсутствие {start:%d.%m.%Y}—{end:%d.%m.%Y} — "
                             f"{workdays} рабочих дней, больше "
                             f"{round_days(rule.threshold_value)}"),
                )
            )

    rule = _rule("annual_ceiling")
    if rule:
        for year, added in split_by_year(date_from, date_to).items():
            planned = planned_days_in_year(employee, year, exclude_entry_id) + added
            ceiling, accumulated_ceiling = annual_ceiling(employee, year)
            if planned > ceiling:
                result.violations.append(
                    RuleViolation(
                        code=rule.code,
                        name=rule.name,
                        message=(f"В {year} году будет запланировано {planned} дней "
                                 f"при допустимых {ceiling} (норма с накопленными — "
                                 f"{accumulated_ceiling}, но не более "
                                 f"{round_days(rule.threshold_value)} в год)"),
                    )
                )

    rule = _rule("no_weekend_captured")
    if rule and not captures_weekend(date_from, date_to):
        result.violations.append(
            RuleViolation(
                code=rule.code,
                name=rule.name,
                message="Период не захватывает ни одного выходного дня",
            )
        )

    rule = _rule("balance_overrun")
    if rule:
        # Собственные черновики тоже расходуют баланс: иначе каждый отпуск
        # по отдельности укладывается в остаток, а вместе превышают его без
        # ограничений. В балансе на экране черновики не списываются.
        available = balance_on(employee, date_from) - own_draft_days(
            employee, date_from, exclude_entry_id)
        overrun = Decimal(days) - available
        if overrun > rule.threshold_value:
            result.violations.append(
                RuleViolation(
                    code=rule.code,
                    name=rule.name,
                    message=f"Запрошено {days} дней при доступных {round_days(available)} — "
                            f"превышение {round_days(overrun)} дней "
                            f"(допустимо {round_days(rule.threshold_value)})",
                )
            )

    result.violations.extend(_check_tag_limits(employee, date_from, date_to, exclude_entry_id))
    return result


def _check_tag_limits(employee: Employee, date_from: date, date_to: date, exclude_entry_id=None):
    """Лимит одновременного отсутствия проверяется по всем тегам сотрудника.

    Пересечением считается хотя бы один общий день.
    Черновики коллег места не занимают.
    """
    rule = _rule("tag_limit_exceeded")
    if not rule:
        return []

    violations = []
    for employee_tag in employee.employee_tags.select_related("tag"):
        tag = employee_tag.tag
        if tag.max_simultaneous_absence is None:
            continue

        colleague_ids = tag.employee_tags.exclude(employee=employee).values_list(
            "employee_id", flat=True
        )
        entries = ScheduleEntry.objects.filter(
            employee_id__in=colleague_ids,
            record_type__in=[ScheduleEntry.RecordType.VACATION,
                             ScheduleEntry.RecordType.DAY_OFF],
            date_from__lte=date_to,
            status__in=[
                ScheduleEntry.Status.PENDING_APPROVAL,
                ScheduleEntry.Status.APPROVED,
            ],
        )
        if exclude_entry_id:
            entries = entries.exclude(id=exclude_entry_id)

        overlapping = sorted(
            (e for e in entries.select_related("employee")
             if e.effective_date_to >= date_from),
            key=lambda e: (e.date_from, e.employee.full_name))
        busy = {e.employee_id for e in overlapping}
        if len(busy) >= tag.max_simultaneous_absence:
            violations.append(
                RuleViolation(
                    code=rule.code,
                    name=rule.name,
                    message=f"По тегу «{tag.name}» уже {len(busy)} из "
                            f"{tag.max_simultaneous_absence} сотрудников в отпуске "
                            f"в период {date_from:%d.%m.%Y}—{date_to:%d.%m.%Y}",
                    colleagues=[
                        {"name": e.employee.full_name, "date_from": e.date_from,
                         "date_to": e.effective_date_to}
                        for e in overlapping
                    ],
                )
            )
    return violations


def validate_annual_plan(employee: Employee, year: int) -> ValidationResult:
    """Проверки уровня годового графика — выполняются при отправке на согласование."""
    result = ValidationResult()

    entries = ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.VACATION,
    ).exclude(status__in=[ScheduleEntry.Status.CANCELLED, ScheduleEntry.Status.REJECTED])

    total_for_year = 0
    max_continuous = 0
    for entry in entries:
        per_year = split_by_year(entry.date_from, entry.effective_date_to)
        total_for_year += per_year.get(year, 0)
        # Непрерывность считается по записи целиком, независимо от границы года.
        max_continuous = max(max_continuous, sum(per_year.values()))

    settings_row = CompanySettings.get_solo()
    norm = employee.annual_vacation_norm_override or settings_row.annual_vacation_norm_days

    rule = _rule("min_annual_days")
    if rule:
        threshold = rule.threshold_value or norm
        if total_for_year < threshold:
            result.violations.append(
                RuleViolation(
                    code=rule.code,
                    name=rule.name,
                    message=f"Запланировано {total_for_year} дней из {round_days(threshold)}",
                )
            )

    rule = _rule("min_continuous_days")
    if rule and max_continuous < rule.threshold_value:
        result.violations.append(
            RuleViolation(
                code=rule.code,
                name=rule.name,
                message=f"Максимальный непрерывный отпуск {max_continuous} дней, "
                        f"требуется {round_days(rule.threshold_value)}",
            )
        )

    return result


# --------------------------------------------------------------------------
# Годовая сводка для интерфейса
# --------------------------------------------------------------------------

def annual_summary(employee: Employee, year: int) -> dict:
    """Показатели, по которым сотрудника заблокируют при отправке графика.

    Интерфейс показывает их постоянно, поэтому считаются они здесь —
    в единственном экземпляре, а не дублируются на клиенте.
    """
    settings_row = CompanySettings.get_solo()
    norm = employee.annual_vacation_norm_override or settings_row.annual_vacation_norm_days
    norm = int(norm)

    entries = ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.VACATION,
    ).exclude(status__in=[ScheduleEntry.Status.CANCELLED,
                          ScheduleEntry.Status.REJECTED])

    planned = 0
    longest = 0
    for entry in entries:
        per_year = split_by_year(entry.date_from, entry.effective_date_to)
        planned += per_year.get(year, 0)
        longest = max(longest, sum(per_year.values()))

    # Накопленное на 1 января расчётного года — потолок планирования
    # считается от него, как зафиксировано при проектировании.
    stock_raw = balance_on(employee, date(year, 1, 1))
    stock = int(stock_raw) if stock_raw > 0 else 0
    ceiling, _ = annual_ceiling(employee, year)

    absence_days = 0
    for entry in ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.ABSENCE,
        date_from__year=year,
    ):
        absence_days += (entry.effective_date_to - entry.date_from).days + 1

    return {
        "year": year,
        "annual_norm": norm,
        "planned_days": planned,
        "against_norm": min(planned, norm),
        "from_accumulated": max(0, planned - norm),
        "accumulated_stock": stock,
        "accumulated_stock_exact": float(stock_raw),
        "ceiling": ceiling,
        "remaining": ceiling - planned,
        "longest_continuous": longest,
        "min_continuous_required": int(
            _rule("min_continuous_days").threshold_value
            if _rule("min_continuous_days") else 0),
        "absence_days": absence_days,
    }


def tag_availability(employee: Employee, year: int) -> list:
    """Занятость по тегам сотрудника — для календаря пересечений."""
    result = []
    for employee_tag in employee.employee_tags.select_related("tag"):
        tag = employee_tag.tag
        colleague_ids = tag.employee_tags.exclude(
            employee=employee).values_list("employee_id", flat=True)
        entries = ScheduleEntry.objects.filter(
            employee_id__in=colleague_ids,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from__year__lte=year,
            status__in=[ScheduleEntry.Status.PENDING_APPROVAL,
                        ScheduleEntry.Status.APPROVED],
        ).select_related("employee")
        result.append({
            "tag_id": str(tag.id),
            "name": tag.name,
            "limit": tag.max_simultaneous_absence,
            "source": employee_tag.source,
            "colleagues_on_leave": [
                {
                    "employee_id": str(e.employee_id),
                    "name": e.employee.full_name,
                    "date_from": e.date_from,
                    "date_to": e.effective_date_to,
                }
                for e in entries
            ],
        })
    return result


# --------------------------------------------------------------------------
# Отгулы за работу в выходные дни
# --------------------------------------------------------------------------

def workday_hours() -> Decimal:
    return CompanySettings.get_solo().workday_hours


def time_off_balance_hours(employee: Employee, on_date: date = None) -> Decimal:
    """Остаток часов отгула.

    Считается суммой регистра, как и отпускной баланс: хранить итог
    отдельным полем означало бы риск расхождения с историей.
    """
    from schedule.models import TimeOffLedger

    rows = TimeOffLedger.objects.filter(employee=employee)
    if on_date:
        rows = rows.filter(entry_date__lte=on_date)
    total = sum((row.hours_delta for row in rows), Decimal("0.00"))
    return total.quantize(Decimal("0.01"))


def time_off_summary(employee: Employee, on_date: date = None) -> dict:
    hours = time_off_balance_hours(employee, on_date)
    day = workday_hours()
    full_days = int(hours // day) if day else 0
    return {
        "hours": float(hours),
        "workday_hours": float(day),
        "available_days": full_days,
        "remainder_hours": float(hours - full_days * day),
    }


def sync_rvd_accrual(entry) -> None:
    """Приводит начисление в регистре в соответствие с записью РВД.

    Вызывается при создании и изменении: администратор может исправить
    часы или переключить тип оплаты, и начисление должно измениться
    вслед за этим, а не остаться от первой версии записи.
    """
    from schedule.models import ScheduleEntry, TimeOffLedger

    if entry.record_type != ScheduleEntry.RecordType.RVD:
        return

    earns = (
        entry.rvd_compensation_type == ScheduleEntry.RvdCompensation.PAY_PLUS_DAY_OFF
        and entry.work_hours
        and entry.status != ScheduleEntry.Status.CANCELLED
    )

    existing = TimeOffLedger.objects.filter(
        related_schedule_entry=entry,
        reason=TimeOffLedger.Reason.RVD_EARNED).first()

    if not earns:
        if existing:
            existing.delete()
        return

    if existing:
        existing.entry_date = entry.date_from
        existing.hours_delta = entry.work_hours
        existing.save(update_fields=["entry_date", "hours_delta"])
    else:
        TimeOffLedger.objects.create(
            employee=entry.employee,
            entry_date=entry.date_from,
            hours_delta=entry.work_hours,
            reason=TimeOffLedger.Reason.RVD_EARNED,
            related_schedule_entry=entry,
            comment=f"РВД {entry.date_from}",
        )


def spend_day_off(entry) -> None:
    """Списание часов за взятый отгул."""
    from schedule.models import ScheduleEntry, TimeOffLedger

    if entry.record_type != ScheduleEntry.RecordType.DAY_OFF:
        return
    if TimeOffLedger.objects.filter(
        related_schedule_entry=entry,
        reason=TimeOffLedger.Reason.DAY_OFF_TAKEN,
    ).exists():
        return

    TimeOffLedger.objects.create(
        employee=entry.employee,
        entry_date=entry.date_from,
        hours_delta=-workday_hours(),
        reason=TimeOffLedger.Reason.DAY_OFF_TAKEN,
        related_schedule_entry=entry,
        comment=f"Отгул {entry.date_from}",
    )


def validate_day_off(employee: Employee, on_date: date,
                     exclude_entry_id=None) -> ValidationResult:
    """Проверки при взятии отгула.

    Правила отпуска здесь не применяются: годовая норма, непрерывность
    и захват выходных к отгулу отношения не имеют.
    """
    from schedule.models import ScheduleEntry

    result = ValidationResult()
    day = workday_hours()

    if isinstance(on_date, date) and (on_date.weekday() in (SATURDAY, SUNDAY)
                                      or on_date in holiday_dates(on_date, on_date)):
        result.violations.append(RuleViolation(
            code="day_off_on_rest_day",
            name="Отгул в нерабочий день",
            message="Выбранный день и так нерабочий — отгул не нужен",
            can_be_approved=False,
        ))

    busy = ScheduleEntry.objects.filter(
        employee=employee,
        date_from__lte=on_date,
        record_type__in=[ScheduleEntry.RecordType.VACATION,
                         ScheduleEntry.RecordType.DAY_OFF],
    # Отклонённое руководителем не занимает день: иначе после отказа
    # отгул на ту же дату нельзя было бы попросить снова.
    ).exclude(status__in=[ScheduleEntry.Status.CANCELLED, ScheduleEntry.Status.REJECTED])
    if exclude_entry_id:
        busy = busy.exclude(id=exclude_entry_id)
    for other in busy:
        if other.effective_date_to >= on_date:
            result.violations.append(RuleViolation(
                code="self_overlap",
                name="Пересечение с другим отсутствием",
                message=f"На эту дату уже внесено: {other.get_record_type_display()}",
                can_be_approved=False,
            ))
            break

    balance = time_off_balance_hours(employee)
    # Уже запланированные отгулы, ещё не списанные регистром.
    planned = ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.DAY_OFF,
        status__in=[ScheduleEntry.Status.DRAFT,
                    ScheduleEntry.Status.PENDING_APPROVAL],
    ).exclude(time_off_records__isnull=False)
    if exclude_entry_id:
        planned = planned.exclude(id=exclude_entry_id)
    available = balance - planned.count() * day

    if available < day:
        result.violations.append(RuleViolation(
            code="day_off_no_balance",
            name="Недостаточно часов отгула",
            message=f"Доступно {available} ч, для отгула нужно {day} ч. "
                    f"Отработайте выходной день с компенсацией «оплата + отгул»",
            can_be_approved=False,
        ))

    result.violations.extend(
        _check_tag_limits(employee, on_date, on_date, exclude_entry_id))
    return result


# --------------------------------------------------------------------------
# Внеплановые отсутствия
# --------------------------------------------------------------------------

def absence_days(date_from: date, date_to: date) -> int:
    """Длительность отсутствия в календарных днях.

    Праздники не вычитаются, в отличие от отпуска: человек отсутствовал
    фактически, а счётчик отражает факт, а не право на дни.
    """
    return (date_to - date_from).days + 1


def validate_absence(employee: Employee, date_from: date, date_to: date,
                     reason, custom_text: str = "",
                     exclude_entry_id=None) -> ValidationResult:
    """Проверки при фиксации внепланового отсутствия.

    Правила отпуска не применяются: отсутствие — это факт, а не
    планирование. Годовая норма, непрерывность и захват выходных
    к нему отношения не имеют.
    """
    from schedule.models import ScheduleEntry

    result = ValidationResult()

    if date_to < date_from:
        result.violations.append(RuleViolation(
            code="bad_period", name="Неверный период",
            message="Дата окончания раньше даты начала",
            can_be_approved=False))
        return result

    if reason is None:
        result.violations.append(RuleViolation(
            code="reason_required", name="Не указана причина",
            message="Причина отсутствия обязательна",
            can_be_approved=False))
    elif reason.is_other and not (custom_text or "").strip():
        result.violations.append(RuleViolation(
            code="custom_reason_required", name="Не указана причина",
            message="При выборе «Другое» нужно описать причину",
            can_be_approved=False))

    # Отсутствие фиксируется постфактум: дата начала в будущем означает
    # либо ошибку, либо попытку запланировать отсутствие, для которого
    # есть отпуск и отгул.
    if date_from > date.today():
        result.violations.append(RuleViolation(
            code="absence_in_future", name="Отсутствие в будущем",
            message="Отсутствие фиксируется по факту. Для будущих дат "
                    "используйте отпуск или отгул",
            can_be_approved=True))

    overlapping = ScheduleEntry.objects.filter(
        employee=employee, date_from__lte=date_to,
    ).exclude(status=ScheduleEntry.Status.CANCELLED)
    if exclude_entry_id:
        overlapping = overlapping.exclude(id=exclude_entry_id)

    for entry in overlapping:
        if entry.effective_date_to < date_from:
            continue
        if entry.record_type == ScheduleEntry.RecordType.ABSENCE:
            result.violations.append(RuleViolation(
                code="absence_overlap", name="Пересечение отсутствий",
                message=f"Период пересекается с отсутствием "
                        f"{entry.date_from}—{entry.effective_date_to}",
                can_be_approved=False))
            break
        if entry.record_type == ScheduleEntry.RecordType.VACATION:
            # Болезнь во время отпуска — законное основание для его
            # продления, но пересчёт делается вручную кадровой службой.
            result.violations.append(RuleViolation(
                code="absence_during_vacation", name="Отсутствие во время отпуска",
                message=f"Период попадает на отпуск "
                        f"{entry.date_from}—{entry.effective_date_to}. "
                        f"Продление отпуска оформляется отдельно",
                can_be_approved=True))
            break

    return result


def absence_summary(employee: Employee, year: int) -> dict:
    """Отдельный счётчик отсутствий — в разрезе причин."""
    from schedule.models import ScheduleEntry

    entries = ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.ABSENCE,
        date_from__year=year,
    ).exclude(status=ScheduleEntry.Status.CANCELLED).select_related("reason")

    by_reason = {}
    total = 0
    for entry in entries:
        days = absence_days(entry.date_from, entry.effective_date_to)
        total += days
        label = entry.reason.name if entry.reason else "Без причины"
        if entry.reason and entry.reason.is_other and entry.custom_reason_text:
            label = f"{label}: {entry.custom_reason_text}"
        row = by_reason.setdefault(label, {"reason": label, "days": 0, "count": 0})
        row["days"] += days
        row["count"] += 1

    return {
        "year": year,
        "total_days": total,
        "episodes": entries.count(),
        "by_reason": sorted(by_reason.values(), key=lambda r: -r["days"]),
    }
