"""Экраны руководителя: «Требует моего решения» и календарь команды (UX 2.3, 2.4).

Списки собираются на сервере: что ждёт решения именно этого человека
и в какие дни лимит по тегу исчерпан — это бизнес-логика, а не отображение.
"""

from datetime import date, timedelta

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee, Tag
from schedule import approvals, calculations
from schedule.models import ApprovalStep, Notification, ScheduleApproval, ScheduleEntry
from schedule.serializers import ApprovalStepSerializer, ViolationSerializer

ACTIVE_STATUSES = [ScheduleEntry.Status.PENDING_APPROVAL, ScheduleEntry.Status.APPROVED]
ABSENCE_TYPES = [ScheduleEntry.RecordType.VACATION, ScheduleEntry.RecordType.DAY_OFF]


def _entry_row(entry):
    return {
        "id": str(entry.id),
        "record_type": entry.record_type,
        "date_from": entry.date_from,
        "date_to": entry.effective_date_to,
        "status": entry.status,
        "vacation_days": (calculations.vacation_days(entry.date_from, entry.effective_date_to)
                          if entry.record_type == ScheduleEntry.RecordType.VACATION else None),
    }


def approvals_awaiting(user):
    """Графики, где очередь решения дошла до этого человека."""
    result = []
    steps = ApprovalStep.objects.filter(
        approver=user, status=ScheduleApproval.Status.PENDING,
        approval__status=ScheduleApproval.Status.PENDING,
    ).select_related("approval", "approval__employee").order_by("approval__created_at")
    for step in steps:
        approval = step.approval
        if approval.steps.filter(order__lt=step.order,
                                 status=ScheduleApproval.Status.PENDING).exists():
            continue  # предыдущий уровень ещё не решил
        entries = ScheduleEntry.objects.filter(
            employee=approval.employee, record_type=ScheduleEntry.RecordType.VACATION,
            date_from__year=approval.year,
        ).exclude(status__in=[ScheduleEntry.Status.CANCELLED, ScheduleEntry.Status.REJECTED])
        rows = [_entry_row(e) for e in entries.order_by("date_from")]
        result.append({
            "approval_id": str(approval.id),
            "step_id": str(step.id),
            "employee_id": str(approval.employee_id),
            "employee_name": approval.employee.full_name,
            "year": approval.year,
            "scope": approval.scope,
            "submitted_at": approval.created_at,
            "planned_days": sum(r["vacation_days"] or 0 for r in rows),
            "entries": rows,
            "steps": ApprovalStepSerializer(approval.steps.order_by("order"), many=True).data,
        })
    return result


def exceptions_awaiting(user):
    """Запросы подтверждения исключений, адресованные этому человеку.

    Источник — уведомления-запросы: в них ссылка на отпуск. Запрос в списке,
    пока отпуск остаётся черновиком с неподтверждённым нарушением.
    """
    entry_ids = Notification.objects.filter(
        recipient=user,
        notification_type=Notification.NotificationType.EXCEPTION_REQUIRED,
        related_entity_id__isnull=False,
    ).values_list("related_entity_id", flat=True)
    entries = ScheduleEntry.objects.filter(
        id__in=set(entry_ids), status=ScheduleEntry.Status.DRAFT,
        record_type=ScheduleEntry.RecordType.VACATION,
    ).select_related("employee").order_by("date_from")
    result = []
    for entry in entries:
        violations = approvals.unresolved_violations(entry)
        if not violations:
            continue
        result.append({
            **_entry_row(entry),
            "employee_id": str(entry.employee_id),
            "employee_name": entry.employee.full_name,
            "violations": ViolationSerializer([v.__dict__ for v in violations], many=True).data,
        })
    return result


class DecisionsView(APIView):
    """«Требует моего решения»: графики на согласовании и запросы исключений."""

    def get(self, request):
        approvals_list = approvals_awaiting(request.user)
        exceptions_list = exceptions_awaiting(request.user)
        return Response({
            "count": len(approvals_list) + len(exceptions_list),
            "approvals": approvals_list,
            "exceptions": exceptions_list,
        })


class ExceptionRejectView(APIView):
    def post(self, request, entry_id):
        entry = get_object_or_404(ScheduleEntry, id=entry_id)
        try:
            approvals.reject_exception(entry, request.user, request.data.get("comment", ""))
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "exception_error"},
                            status=status.HTTP_409_CONFLICT)
        return Response({"status": entry.status})


def _parse_date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return None


def _pending_approval(entry):
    """Согласование, в котором сейчас находится отпуск, — последнее открытое за его год."""
    return (ScheduleApproval.objects
            .filter(employee_id=entry.employee_id, year=entry.date_from.year,
                    status=ScheduleApproval.Status.PENDING)
            .order_by("-created_at").first())


def _current_step(approval):
    """Шаг, на котором стоит решение: первый нерешённый по порядку."""
    return (approval.steps.filter(status=ScheduleApproval.Status.PENDING)
            .select_related("approver").order_by("order").first())


def _exception_approver_name(entry):
    """Кому ушёл запрос подтверждения исключения по черновику."""
    note = (Notification.objects
            .filter(notification_type=Notification.NotificationType.EXCEPTION_REQUIRED,
                    related_entity_id=entry.id)
            .select_related("recipient").order_by("-created_at").first())
    return note.recipient.full_name if note else None


def _waiting_for(entries):
    """Короткая подпись «сейчас у кого» для неутверждённых отпусков.

    Черновик ждёт руководителя, только пока у него есть неподтверждённое
    нарушение и по нему ушёл запрос; проверка правил дорогая, поэтому
    выполняется лишь для черновиков, по которым запрос был.
    """
    result = {}
    requested = set(Notification.objects.filter(
        notification_type=Notification.NotificationType.EXCEPTION_REQUIRED,
        related_entity_id__in=[e.id for e in entries if e.status == ScheduleEntry.Status.DRAFT],
    ).values_list("related_entity_id", flat=True))
    approvals_cache = {}
    for entry in entries:
        if entry.record_type != ScheduleEntry.RecordType.VACATION:
            continue
        if entry.status == ScheduleEntry.Status.PENDING_APPROVAL:
            key = (entry.employee_id, entry.date_from.year)
            if key not in approvals_cache:
                approval = _pending_approval(entry)
                step = _current_step(approval) if approval else None
                approvals_cache[key] = step.approver.full_name if step else None
            result[entry.id] = approvals_cache[key]
        elif (entry.status == ScheduleEntry.Status.DRAFT and entry.id in requested
              and approvals.unresolved_violations(entry)):
            result[entry.id] = _exception_approver_name(entry)
    return result


def _calendar_scope(user, scope):
    """Кого показывает календарь и какие записи видны.

    Возвращает (сотрудники, id своей структуры). По умолчанию руководитель
    видит свою ветку. В режиме «все подразделения» — всю компанию, но по
    чужим подразделениям только отпуска на согласовании и утверждённые:
    черновик — личный план, он ещё меняется. Администратор видит всех всегда.
    """
    employees = Employee.objects.filter(status=Employee.Status.ACTIVE).select_related(
        "org_unit")
    if user.is_admin:
        return employees, None
    own = set(user.visible_employee_ids())
    if scope == "all":
        return employees, own
    return employees.filter(id__in=own), own


def _entry_visible(entry, own):
    return own is None or entry.employee_id in own or entry.status in ACTIVE_STATUSES


class TeamCalendarView(APIView):
    """Сводный календарь: строки — сотрудники, отпуска и дни исчерпанного лимита.

    Доступен руководителям и администратору (ФЗ 4.7). Руководитель по умолчанию
    видит свою структуру, по выбору — график всех подразделений
    (`scope=all`, см. `_calendar_scope`).
    Лимит считается по всем сотрудникам с тегом, а не только по видимым:
    он общий для компании.
    """

    def get(self, request):
        user = request.user
        if not (user.is_admin or user.managed_units.exists()):
            return Response({"detail": "Календарь команды доступен руководителям"},
                            status=status.HTTP_403_FORBIDDEN)

        today = date.today()
        date_from = _parse_date(request.query_params.get("date_from"), today.replace(day=1))
        date_to = _parse_date(request.query_params.get("date_to"),
                              date_from and date_from + timedelta(days=30))
        if not date_from or not date_to or date_to < date_from:
            return Response({"detail": "Неверный период"}, status=status.HTTP_400_BAD_REQUEST)
        if (date_to - date_from).days > 92:
            return Response({"detail": "Период не длиннее квартала"},
                            status=status.HTTP_400_BAD_REQUEST)

        scope = "all" if request.query_params.get("scope") == "all" else "mine"
        employees, own = _calendar_scope(user, scope)

        tag = None
        tag_id = request.query_params.get("tag_id")
        if tag_id:
            tag = get_object_or_404(Tag, id=tag_id)
            employees = employees.filter(employee_tags__tag=tag)
        employees = list(employees.distinct().order_by("org_unit__name", "full_name"))

        entries = ScheduleEntry.objects.filter(
            employee__in=employees, record_type__in=ABSENCE_TYPES,
            date_from__lte=date_to,
        ).exclude(status__in=[ScheduleEntry.Status.CANCELLED, ScheduleEntry.Status.REJECTED])
        entries = [e for e in entries
                   if e.effective_date_to >= date_from and _entry_visible(e, own)]
        waiting = _waiting_for(entries)
        by_employee = {}
        for entry in entries:
            by_employee.setdefault(entry.employee_id, []).append(
                {**_entry_row(entry), "waiting_for": waiting.get(entry.id)})

        return Response({
            "date_from": date_from,
            "date_to": date_to,
            "scope": "all" if own is None else scope,
            "can_view_all": own is not None,
            "tag": ({"id": str(tag.id), "name": tag.name,
                     "limit": tag.max_simultaneous_absence} if tag else None),
            "limit_reached": _limit_reached_days(tag, date_from, date_to) if tag else [],
            "rows": [
                {"employee_id": str(e.id), "full_name": e.full_name,
                 "org_unit": e.org_unit.name if e.org_unit else None,
                 "own": own is None or e.id in own,
                 "entries": sorted(by_employee.get(e.id, []), key=lambda r: r["date_from"])}
                for e in employees
            ],
        })


class TeamEntryDetailView(APIView):
    """Карточка отпуска в календаре команды: статус и у кого он на решении.

    Видимость — та же, что в календаре: своя структура целиком, чужие
    подразделения — только отпуска на согласовании и утверждённые.
    Комментарии согласующих по чужим подразделениям не показываются.
    """

    def get(self, request, entry_id):
        user = request.user
        if not (user.is_admin or user.managed_units.exists()):
            return Response({"detail": "Календарь команды доступен руководителям"},
                            status=status.HTTP_403_FORBIDDEN)
        entry = get_object_or_404(
            ScheduleEntry.objects.select_related("employee", "employee__org_unit"),
            id=entry_id, record_type__in=ABSENCE_TYPES)
        _, own = _calendar_scope(user, "all")
        if entry.status in (ScheduleEntry.Status.CANCELLED, ScheduleEntry.Status.REJECTED) \
                or not _entry_visible(entry, own):
            return Response({"detail": "Не найдено"}, status=status.HTTP_404_NOT_FOUND)
        is_own = own is None or entry.employee_id in own

        steps = []
        if entry.status == ScheduleEntry.Status.PENDING_APPROVAL:
            approval = _pending_approval(entry)
            if approval:
                current = _current_step(approval)
                for step in ApprovalStepSerializer(
                        approval.steps.select_related("approver").order_by("order"),
                        many=True).data:
                    step["is_current"] = bool(current and step["id"] == str(current.id))
                    if not is_own:
                        step["comment"] = ""
                    steps.append(step)

        exception = None
        if entry.status == ScheduleEntry.Status.DRAFT \
                and entry.record_type == ScheduleEntry.RecordType.VACATION:
            violations = approvals.unresolved_violations(entry)
            approver = _exception_approver_name(entry) if violations else None
            if approver:
                exception = {
                    "approver_name": approver,
                    "violations": ViolationSerializer(
                        [v.__dict__ for v in violations], many=True).data,
                }

        return Response({
            **_entry_row(entry),
            "employee_name": entry.employee.full_name,
            "org_unit": entry.employee.org_unit.name if entry.employee.org_unit else None,
            "steps": steps,
            "exception": exception,
        })


def _limit_reached_days(tag, date_from, date_to):
    """Дни периода, где число отсутствующих с тегом достигло лимита.

    Учитываются те же записи, что в правиле tag_limit_exceeded: отпуска
    и отгулы на согласовании и утверждённые; черновики места не занимают.
    """
    if tag.max_simultaneous_absence is None:
        return []
    member_ids = tag.employee_tags.values_list("employee_id", flat=True)
    entries = ScheduleEntry.objects.filter(
        employee_id__in=member_ids, record_type__in=ABSENCE_TYPES,
        status__in=ACTIVE_STATUSES, date_from__lte=date_to,
    )
    busy = {}
    for entry in entries:
        start = max(entry.date_from, date_from)
        end = min(entry.effective_date_to, date_to)
        current = start
        while current <= end:
            busy.setdefault(current, set()).add(entry.employee_id)
            current += timedelta(days=1)
    return [{"date": day, "count": len(people)}
            for day, people in sorted(busy.items())
            if len(people) >= tag.max_simultaneous_absence]
