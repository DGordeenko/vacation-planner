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


class TeamCalendarView(APIView):
    """Сводный календарь: строки — сотрудники, отпуска и дни исчерпанного лимита.

    Доступен руководителям в пределах их структуры и администратору (ФЗ 4.7).
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

        employees = Employee.objects.filter(status=Employee.Status.ACTIVE).select_related(
            "org_unit")
        if not user.is_admin:
            employees = employees.filter(id__in=user.visible_employee_ids())

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
        by_employee = {}
        for entry in entries:
            if entry.effective_date_to >= date_from:
                by_employee.setdefault(entry.employee_id, []).append(_entry_row(entry))

        return Response({
            "date_from": date_from,
            "date_to": date_to,
            "tag": ({"id": str(tag.id), "name": tag.name,
                     "limit": tag.max_simultaneous_absence} if tag else None),
            "limit_reached": _limit_reached_days(tag, date_from, date_to) if tag else [],
            "rows": [
                {"employee_id": str(e.id), "full_name": e.full_name,
                 "org_unit": e.org_unit.name if e.org_unit else None,
                 "entries": sorted(by_employee.get(e.id, []), key=lambda r: r["date_from"])}
                for e in employees
            ],
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
