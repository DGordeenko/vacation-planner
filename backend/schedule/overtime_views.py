"""API сверхурочных. Логика — в schedule/overtime.py."""

import csv
import io

from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from audit.drf import RecordAuditActor
from config.params import parse_year
from schedule import overtime
from schedule.models import ScheduleEntry
from schedule.serializers import (
    OvertimeCheckSerializer,
    OvertimeInputSerializer,
    OvertimeSerializer,
)

REPORT_COLUMNS = [("full_name", "ФИО"), ("employee_number", "Табельный номер"),
                  ("date", "Дата"), ("day_type", "Тип дня"), ("period", "Период работы")]


class OvertimeViewSet(viewsets.ModelViewSet):
    """Сверхурочные: видимость — сам, своя структура, администратор и HR — все."""

    serializer_class = OvertimeSerializer
    permission_classes = [RecordAuditActor] + viewsets.ModelViewSet.permission_classes
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        rows = ScheduleEntry.objects.filter(
            record_type=ScheduleEntry.RecordType.OVERTIME,
            employee_id__in=self.request.user.visible_employee_ids(),
        ).select_related("employee", "employee__org_unit", "created_by")
        params = self.request.query_params
        if params.get("employee_id"):
            rows = rows.filter(employee_id=params["employee_id"])
        if params.get("year"):
            rows = rows.filter(date_from__year=parse_year(params["year"]))
        if params.get("month"):
            rows = rows.filter(date_from__month=int(params["month"])
                               if params["month"].isdigit() else 0)
        if params.get("mine"):
            rows = rows.filter(employee=self.request.user)
        return rows.order_by("-date_from", "-work_time_from")

    def _input(self, request, serializer=OvertimeInputSerializer):
        data = serializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        employee = values.get("employee") or request.user
        if not overtime.can_record_for(request.user, employee):
            raise PermissionDenied("Вносить сверхурочные можно себе и сотрудникам своей "
                                   "структуры")
        return employee, values

    @action(detail=False, methods=["post"])
    def validate(self, request):
        """Предпросмотр: часы, ошибки и предупреждения о пределах — до сохранения."""
        employee, values = self._input(request, OvertimeCheckSerializer)
        result = overtime.check(employee, values["date"], values["time_from"],
                                values["time_to"])
        return Response({"hours": float(result.hours), "errors": result.errors,
                         "warnings": result.warnings})

    def create(self, request, *args, **kwargs):
        employee, values = self._input(request)
        try:
            entry, warnings = overtime.create(
                employee, request.user, values["date"], values["time_from"],
                values["time_to"], values["task_link"], values["compensation"])
        except overtime.OvertimeError as error:
            return Response({"detail": str(error), "code": "overtime_blocked"},
                            status=status.HTTP_409_CONFLICT)
        return Response({**OvertimeSerializer(entry).data, "warnings": warnings},
                        status=status.HTTP_201_CREATED)

    def _decide(self, request, approved):
        entry = self.get_object()
        try:
            overtime.decide(entry, request.user, approved, request.data.get("comment"))
        except PermissionError as error:
            return Response({"detail": str(error)}, status=status.HTTP_403_FORBIDDEN)
        except overtime.OvertimeError as error:
            return Response({"detail": str(error), "code": "overtime_decided"},
                            status=status.HTTP_409_CONFLICT)
        return Response(OvertimeSerializer(entry).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, True)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, False)

    def destroy(self, request, *args, **kwargs):
        """Отменить запись до согласования может автор или сам сотрудник.

        Согласованную — только администратор: начисленный отгул мог быть
        уже потрачен, а оплата — передана в расчёт.
        """
        entry = self.get_object()
        if not request.user.is_admin:
            if request.user.id not in (entry.employee_id, entry.created_by_id):
                return Response({"detail": "Отменить запись может сотрудник или её автор"},
                                status=status.HTTP_403_FORBIDDEN)
            if entry.status == ScheduleEntry.Status.APPROVED:
                return Response({"detail": "Согласованные сверхурочные отменяет "
                                           "администратор", "code": "overtime_locked"},
                                status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)  # регистр очистится каскадом

    @action(detail=False, methods=["get"])
    def report(self, request):
        """Согласованные сверхурочные к оплате: для расчёта зарплаты.

        Только поля, нужные бухгалтерии. `?format=csv` — файл для Excel
        (разделитель «;», UTF-8 с BOM). Администратор и HR.
        """
        user = request.user
        if not (user.is_admin or user.is_hr):
            return Response({"detail": "Отчёт доступен администратору и HR"},
                            status=status.HTTP_403_FORBIDDEN)
        rows = ScheduleEntry.objects.filter(
            record_type=ScheduleEntry.RecordType.OVERTIME,
            status=ScheduleEntry.Status.APPROVED,
            overtime_compensation=ScheduleEntry.OvertimeCompensation.PAY,
            date_from__year=parse_year(request.query_params.get("year")),
        ).select_related("employee").order_by("employee__full_name", "date_from",
                                              "work_time_from")
        month = request.query_params.get("month")
        if month:
            rows = rows.filter(date_from__month=int(month) if month.isdigit() else 0)
        data = overtime.report_rows(rows)
        if request.query_params.get("export") == "csv":
            return _csv(data)
        return Response(data)


def _csv(rows):
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([title for _key, title in REPORT_COLUMNS])
    for row in rows:
        writer.writerow([row["date"].strftime("%d.%m.%Y") if key == "date" else row[key]
                         for key, _title in REPORT_COLUMNS])
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="overtime.csv"'
    return response
