"""Согласование графика и подтверждение исключений.

Релиз 1: один уровень согласования — непосредственный руководитель.
Цепочка строится функцией build_chain, поэтому переход на четыре уровня
в релизе 2 сводится к изменению одной функции.
"""

from django.db import transaction
from django.utils import timezone

from employees.models import Employee
from schedule import calculations
from schedule.models import (
    ApprovalStep,
    ExceptionApproval,
    Notification,
    ScheduleApproval,
    ScheduleEntry,
    ValidationRule,
)

LEVEL_BY_UNIT_TYPE = {
    "group": ApprovalStep.Level.GROUP,
    "department": ApprovalStep.Level.DEPARTMENT,
    "division_unit": ApprovalStep.Level.DIVISION_UNIT,
    "division": ApprovalStep.Level.DIVISION,
}


class ApprovalError(Exception):
    """Ошибка бизнес-правила согласования — превращается в 409 на уровне API."""


def build_chain(employee, levels=None):
    """Цепочка согласующих снизу вверх по оргструктуре.

    Число уровней задаётся настройкой approval_levels (по умолчанию 4:
    группа, отдел, подразделение, дивизион). Один и тот же человек
    попадает в цепочку не более одного раза: он может руководить
    несколькими уровнями сразу, и просить его согласовать дважды
    бессмысленно.

    Если уровней в структуре меньше требуемого — цепочка завершается
    администратором. Без этого график сотрудника у вершины структуры
    было бы некому утвердить.
    """
    from company.models import CompanySettings

    if levels is None:
        levels = CompanySettings.get_solo().approval_levels

    chain = []
    seen = {employee.id}
    unit = employee.org_unit

    while unit is not None and len(chain) < levels:
        manager = unit.manager
        if manager and manager.id not in seen:
            seen.add(manager.id)
            chain.append((manager, LEVEL_BY_UNIT_TYPE.get(
                unit.unit_type, ApprovalStep.Level.GROUP)))
        unit = unit.parent

    # Администратор подключается, только если руководителя нет вовсе:
    # иначе в компании с двумя уровнями он визировал бы все графики
    # и стал бы узким местом кампании. Если уровни есть, но их меньше
    # требуемого, цепочка просто короче — это нормальная структура.
    if not chain:
        admin = Employee.objects.filter(
            is_admin=True, status=Employee.Status.ACTIVE
        ).exclude(id__in=seen).first()
        if admin:
            chain.append((admin, ApprovalStep.Level.ADMIN))

    return chain


def notify(recipient, notification_type, message, entity=None):
    Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        message=message,
        related_entity_type=entity.__class__.__name__ if entity else "",
        related_entity_id=entity.id if entity else None,
    )


@transaction.atomic
def approve_exception(entry, rule_code, approver, comment):
    """Подтверждение исключения по заблокированному правилу."""
    if not comment or not comment.strip():
        raise ApprovalError("Комментарий обязателен при подтверждении исключения")

    rule = ValidationRule.objects.filter(code=rule_code).first()
    if rule is None:
        raise ApprovalError(f"Правило «{rule_code}» не найдено")

    manager = entry.employee.manager
    if not (approver.is_admin or (manager and approver.id == manager.id)):
        raise ApprovalError(
            "Подтверждать исключение может непосредственный руководитель или администратор")

    approval = ExceptionApproval.objects.create(
        schedule_entry=entry, rule=rule, approved_by=approver, comment=comment.strip())

    if rule.code == "max_continuous_workdays":
        # Правило срабатывает на всё непрерывное отсутствие, а не на одну
        # запись: подтверждение распространяется на соседние черновики того же
        # отсутствия, иначе каждый пришлось бы подтверждать отдельно. Если
        # отсутствие потом удлинят, новая запись потребует нового подтверждения.
        start, end = calculations.continuous_block(
            entry.employee, entry.date_from, entry.effective_date_to, entry.id)
        neighbours = ScheduleEntry.objects.filter(
            employee=entry.employee, record_type=ScheduleEntry.RecordType.VACATION,
            status=ScheduleEntry.Status.DRAFT, date_from__gte=start, date_from__lte=end,
        ).exclude(id=entry.id).exclude(exception_approvals__rule=rule)
        for neighbour in neighbours:
            ExceptionApproval.objects.create(
                schedule_entry=neighbour, rule=rule, approved_by=approver,
                comment=comment.strip())

    notify(entry.employee, Notification.NotificationType.APPROVAL_DECIDED,
           f"{approver.full_name} подтвердил исключение «{rule.name}» "
           f"для отпуска {entry.date_from}—{entry.effective_date_to}", entry)
    return approval


def exception_approver(employee):
    """Кто подтверждает исключение: непосредственный руководитель,
    у вершины структуры — администратор (как в цепочке согласования)."""
    if employee.manager:
        return employee.manager
    chain = build_chain(employee)
    return chain[0][0] if chain else None


def request_exception(entry, violations):
    """Запрос подтверждения исключения по черновику отпуска.

    Уведомление ссылается на сам отпуск: руководителю есть что подтвердить.
    Повторный запрос по той же записи не дублирует непрочитанное уведомление.
    """
    approver = exception_approver(entry.employee)
    if approver is None:
        return None
    if Notification.objects.filter(
        recipient=approver,
        notification_type=Notification.NotificationType.EXCEPTION_REQUIRED,
        related_entity_id=entry.id, is_read=False,
    ).exists():
        return approver
    rules = "; ".join(v.message for v in violations)
    notify(approver, Notification.NotificationType.EXCEPTION_REQUIRED,
           f"{entry.employee.full_name} просит подтвердить отпуск "
           f"{entry.date_from:%d.%m.%Y}—{entry.effective_date_to:%d.%m.%Y}: {rules}",
           entry)
    return approver


def entries_awaiting_exception(employee, year):
    """Черновики года с неподтверждёнными нарушениями — отправка с ними запрещена."""
    entries = ScheduleEntry.objects.filter(
        employee=employee, record_type=ScheduleEntry.RecordType.VACATION,
        date_from__year=year, status=ScheduleEntry.Status.DRAFT)
    result = []
    for entry in entries:
        violations = unresolved_violations(entry)
        if violations:
            result.append((entry, violations))
    return result


def _raise_if_awaiting_exception(employee, year):
    awaiting = entries_awaiting_exception(employee, year)
    if not awaiting:
        return
    for entry, violations in awaiting:
        request_exception(entry, violations)
    periods = ", ".join(
        f"{entry.date_from:%d.%m}—{entry.effective_date_to:%d.%m}" for entry, _ in awaiting)
    raise ApprovalError(
        f"Отпуска ждут подтверждения руководителя: {periods}. "
        "Отправить график можно после его решения")


@transaction.atomic
def reject_exception(entry, approver, comment):
    """Отказ в исключении: отпуск отклоняется, сотрудник получает причину.

    Отпуск переводится в «Отклонён», а не удаляется: сотрудник видит,
    какие даты не согласованы и почему, а запрос уходит из списка
    решений руководителя.
    """
    if not comment or not comment.strip():
        raise ApprovalError("Комментарий обязателен при отказе")
    if entry.status != ScheduleEntry.Status.DRAFT:
        raise ApprovalError("Решение по этому отпуску уже принято")
    manager = entry.employee.manager
    if not (approver.is_admin or (manager and approver.id == manager.id)):
        raise ApprovalError(
            "Отказать в исключении может непосредственный руководитель или администратор")
    entry.status = ScheduleEntry.Status.REJECTED
    entry.rejection_comment = comment.strip()
    entry.save(update_fields=["status", "rejection_comment", "updated_at"])
    notify(entry.employee, Notification.NotificationType.APPROVAL_DECIDED,
           f"{approver.full_name} не подтвердил отпуск "
           f"{entry.date_from:%d.%m.%Y}—{entry.effective_date_to:%d.%m.%Y}: "
           f"{comment.strip()}", entry)
    return entry


def unresolved_violations(entry):
    """Нарушения, по которым ещё нет подтверждения руководителя."""
    result = calculations.validate_entry(
        entry.employee, entry.date_from, entry.effective_date_to,
        entry.record_type, exclude_entry_id=entry.id)
    approved_codes = set(
        entry.exception_approvals.values_list("rule__code", flat=True))
    return [v for v in result.violations if v.code not in approved_codes]


def submit_year(employee, year, actor):
    """Отправка годового графика на согласование.

    Проверки выполняются до открытия транзакции: уведомление руководителю
    о заблокированной отправке должно пережить отказ, иначе он не узнает,
    что от него ждут подтверждения исключения.
    """
    if ScheduleApproval.objects.filter(
        employee=employee, year=year, status=ScheduleApproval.Status.PENDING
    ).exists():
        raise ApprovalError("График уже отправлен и ожидает решения")

    entries = ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.VACATION,
        date_from__year=year,
    ).exclude(status__in=[ScheduleEntry.Status.CANCELLED,
                          ScheduleEntry.Status.REJECTED])
    if not entries.exists():
        raise ApprovalError("В графике нет ни одного отпуска")

    _raise_if_awaiting_exception(employee, year)

    plan = calculations.validate_annual_plan(employee, year)
    approved_codes = set(
        ExceptionApproval.objects.filter(schedule_entry__employee=employee)
        .values_list("rule__code", flat=True))
    blocking = [v for v in plan.violations if v.code not in approved_codes]

    chain = build_chain(employee)
    if not chain:
        raise ApprovalError(
            "Не найден согласующий: у сотрудника нет руководителя и в системе нет администратора")

    if blocking:
        # Годовые правила подтверждает тот же руководитель — но отдельным
        # действием, чтобы решение было осознанным и с комментарием.
        for violation in blocking:
            notify(chain[0][0], Notification.NotificationType.EXCEPTION_REQUIRED,
                   f"{employee.full_name}: {violation.message}", employee)
        raise ApprovalError(
            "График не проходит годовые проверки: "
            + "; ".join(v.message for v in blocking))

    with transaction.atomic():
        approval = ScheduleApproval.objects.create(employee=employee, year=year)
        for order, (approver, level) in enumerate(chain, start=1):
            ApprovalStep.objects.create(
                approval=approval, order=order, level=level, approver=approver)

        entries.update(status=ScheduleEntry.Status.PENDING_APPROVAL)

        notify(chain[0][0], Notification.NotificationType.APPROVAL_REQUIRED,
               f"{employee.full_name} отправил график на {year} год", approval)
    return approval


@transaction.atomic
def decide_step(step, actor, approved, comment=""):
    """Решение по шагу согласования."""
    if step.approval.status != ScheduleApproval.Status.PENDING:
        raise ApprovalError("Согласование уже завершено")
    if step.status != ScheduleApproval.Status.PENDING:
        raise ApprovalError("По этому шагу решение уже принято")
    if not (actor.is_admin or actor.id == step.approver_id):
        raise ApprovalError("Решение принимает назначенный согласующий")
    if not approved and not (comment or "").strip():
        raise ApprovalError("Комментарий обязателен при отклонении")

    previous = step.approval.steps.filter(
        order__lt=step.order, status=ScheduleApproval.Status.PENDING)
    if previous.exists():
        raise ApprovalError("Не принято решение на предыдущем уровне")

    step.status = (ScheduleApproval.Status.APPROVED if approved
                   else ScheduleApproval.Status.REJECTED)
    step.comment = (comment or "").strip()
    step.decided_at = timezone.now()
    step.save(update_fields=["status", "comment", "decided_at"])

    approval = step.approval
    entries = ScheduleEntry.objects.filter(
        employee=approval.employee,
        record_type=ScheduleEntry.RecordType.VACATION,
        date_from__year=approval.year,
        status=ScheduleEntry.Status.PENDING_APPROVAL)

    if not approved:
        approval.status = ScheduleApproval.Status.REJECTED
        approval.save(update_fields=["status"])
        if approval.scope == ScheduleApproval.Scope.AMENDMENT:
            # Отказ по правкам возвращает утверждённый график целиком.
            _restore_snapshot(approval)
        else:
            entries.update(status=ScheduleEntry.Status.DRAFT)
        notify(approval.employee, Notification.NotificationType.APPROVAL_DECIDED,
               f"{actor.full_name} отклонил график на {approval.year} год: {step.comment}",
               approval)
        return approval

    if not approval.steps.filter(status=ScheduleApproval.Status.PENDING).exists():
        approval.status = ScheduleApproval.Status.APPROVED
        approval.save(update_fields=["status"])
        entries.update(status=ScheduleEntry.Status.APPROVED)
        notify(approval.employee, Notification.NotificationType.APPROVAL_DECIDED,
               f"График на {approval.year} год согласован", approval)
    else:
        next_step = approval.steps.filter(
            status=ScheduleApproval.Status.PENDING).first()
        notify(next_step.approver, Notification.NotificationType.APPROVAL_REQUIRED,
               f"{approval.employee.full_name}: график на {approval.year} год "
               f"ожидает вашего решения", approval)
    return approval


@transaction.atomic
def withdraw(approval, actor):
    """Отзыв графика сотрудником до решения руководителя."""
    if approval.status != ScheduleApproval.Status.PENDING:
        raise ApprovalError("Отозвать можно только график на согласовании")
    if not (actor.is_admin or actor.id == approval.employee_id):
        raise ApprovalError("Отозвать график может только его владелец")

    approval.status = ScheduleApproval.Status.WITHDRAWN
    approval.save(update_fields=["status"])
    if approval.scope == ScheduleApproval.Scope.AMENDMENT:
        _restore_snapshot(approval)
    else:
        ScheduleEntry.objects.filter(
            employee=approval.employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from__year=approval.year,
            status=ScheduleEntry.Status.PENDING_APPROVAL,
        ).update(status=ScheduleEntry.Status.DRAFT)

    for step in approval.steps.filter(status=ScheduleApproval.Status.PENDING):
        notify(step.approver, Notification.NotificationType.APPROVAL_DECIDED,
               f"{approval.employee.full_name} отозвал график на {approval.year} год",
               approval)
    return approval


# --------------------------------------------------------------------------
# Актуализация утверждённого графика
# --------------------------------------------------------------------------

AMENDMENT_LEVELS = 2


def _year_entries(employee, year):
    return ScheduleEntry.objects.filter(
        employee=employee,
        record_type=ScheduleEntry.RecordType.VACATION,
        date_from__year=year,
    ).exclude(status=ScheduleEntry.Status.CANCELLED)


def _take_snapshot(employee, year):
    return [
        {
            "date_from": entry.date_from.isoformat(),
            "date_to": entry.effective_date_to.isoformat(),
            "status": entry.status,
            "extended_by_holiday_days": entry.extended_by_holiday_days,
        }
        for entry in _year_entries(employee, year)
    ]


def _restore_snapshot(approval):
    """Возврат графика к утверждённому состоянию.

    Текущие записи года удаляются и пересоздаются из снимка: это проще
    и надёжнее, чем отслеживать отдельные правки, и гарантирует, что после
    отказа сотрудник получит ровно тот график, который был утверждён.
    """
    from datetime import date as date_type

    employee, year = approval.employee, approval.year
    _year_entries(employee, year).delete()
    for row in approval.snapshot:
        ScheduleEntry.objects.create(
            employee=employee,
            created_by=employee,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date_type.fromisoformat(row["date_from"]),
            date_to=date_type.fromisoformat(row["date_to"]),
            status=ScheduleEntry.Status.APPROVED,
            extended_by_holiday_days=row.get("extended_by_holiday_days", 0),
        )


def approved_approval(employee, year):
    return ScheduleApproval.objects.filter(
        employee=employee, year=year, status=ScheduleApproval.Status.APPROVED
    ).order_by("-created_at").first()


def active_amendment(employee, year):
    return ScheduleApproval.objects.filter(
        employee=employee, year=year,
        scope=ScheduleApproval.Scope.AMENDMENT,
        status__in=[ScheduleApproval.Status.OPEN,
                    ScheduleApproval.Status.PENDING],
    ).order_by("-created_at").first()


@transaction.atomic
def open_amendment(employee, year, actor):
    """Открытие утверждённого графика для правок."""
    if not (actor.is_admin or actor.id == employee.id):
        raise ApprovalError("Открыть график для правок может только его владелец")
    if approved_approval(employee, year) is None:
        raise ApprovalError("График на этот год ещё не утверждён — правьте его как есть")
    if active_amendment(employee, year):
        raise ApprovalError("Правки уже открыты")

    amendment = ScheduleApproval.objects.create(
        employee=employee, year=year,
        scope=ScheduleApproval.Scope.AMENDMENT,
        status=ScheduleApproval.Status.OPEN,
        snapshot=_take_snapshot(employee, year),
    )
    _year_entries(employee, year).update(status=ScheduleEntry.Status.DRAFT)
    return amendment


@transaction.atomic
def cancel_amendment(amendment, actor):
    """Отказ от правок до отправки — график возвращается к утверждённому."""
    if amendment.status != ScheduleApproval.Status.OPEN:
        raise ApprovalError("Отменить можно только неотправленные правки")
    if not (actor.is_admin or actor.id == amendment.employee_id):
        raise ApprovalError("Отменить правки может только владелец графика")

    _restore_snapshot(amendment)
    amendment.status = ScheduleApproval.Status.WITHDRAWN
    amendment.save(update_fields=["status"])
    return amendment


def _changes_summary(amendment):
    """Что изменилось относительно утверждённого графика."""
    was = {(row["date_from"], row["date_to"]) for row in amendment.snapshot}
    now = {
        (entry.date_from.isoformat(), entry.effective_date_to.isoformat())
        for entry in _year_entries(amendment.employee, amendment.year)
    }
    return {
        "added": sorted(now - was),
        "removed": sorted(was - now),
    }


@transaction.atomic
def submit_amendment(amendment, actor):
    """Отправка правок на согласование двумя уровнями выше сотрудника."""
    if amendment.status != ScheduleApproval.Status.OPEN:
        raise ApprovalError("Правки уже отправлены или отменены")
    if not (actor.is_admin or actor.id == amendment.employee_id):
        raise ApprovalError("Отправить правки может только владелец графика")

    employee, year = amendment.employee, amendment.year
    _raise_if_awaiting_exception(employee, year)
    changes = _changes_summary(amendment)
    if not changes["added"] and not changes["removed"]:
        raise ApprovalError("График не изменился — отправлять нечего")

    plan = calculations.validate_annual_plan(employee, year)
    approved_codes = set(
        ExceptionApproval.objects.filter(schedule_entry__employee=employee)
        .values_list("rule__code", flat=True))
    blocking = [v for v in plan.violations if v.code not in approved_codes]
    if blocking:
        raise ApprovalError(
            "Изменённый график не проходит годовые проверки: "
            + "; ".join(v.message for v in blocking))

    chain = build_chain_for_amendment(employee)
    if not chain:
        raise ApprovalError("Не найдены согласующие для изменения графика")

    for order, (approver, level) in enumerate(chain, start=1):
        ApprovalStep.objects.create(
            approval=amendment, order=order, level=level, approver=approver)

    amendment.status = ScheduleApproval.Status.PENDING
    amendment.save(update_fields=["status"])
    _year_entries(employee, year).update(status=ScheduleEntry.Status.PENDING_APPROVAL)

    description = []
    if changes["added"]:
        description.append(f"добавлено периодов: {len(changes['added'])}")
    if changes["removed"]:
        description.append(f"убрано: {len(changes['removed'])}")
    notify(chain[0][0], Notification.NotificationType.APPROVAL_REQUIRED,
           f"{employee.full_name} изменил утверждённый график на {year} год "
           f"({', '.join(description)})", amendment)
    return amendment


def build_chain_for_amendment(employee):
    """Два уровня руководства выше сотрудника.

    Правка утверждённого документа требует двух подписей. Если уровней
    в структуре меньше, цепочка дополняется администратором — в отличие
    от первичного согласования, где короткая цепочка допустима: там
    документ ещё только создаётся, здесь — пересматривается утверждённый.
    """
    chain = build_chain(employee, levels=AMENDMENT_LEVELS)
    if len(chain) < AMENDMENT_LEVELS:
        taken = {person.id for person, _ in chain} | {employee.id}
        admin = Employee.objects.filter(
            is_admin=True, status=Employee.Status.ACTIVE
        ).exclude(id__in=taken).first()
        if admin:
            chain.append((admin, ApprovalStep.Level.ADMIN))
    return chain
