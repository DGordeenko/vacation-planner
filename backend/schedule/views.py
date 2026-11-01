import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from company.models import AbsenceReason, CompanySettings, Holiday
from config.params import parse_date, parse_year
from config.permissions import ADMIN_ONLY, ADMIN_WRITES, visible_employee_or_403
from employees.models import Employee
from schedule import approvals, calculations
from schedule.models import (
    ApprovalStep,
    Notification,
    ScheduleApproval,
    ScheduleEntry,
    ValidationRule,
)
from schedule.serializers import (
    AbsenceReasonSerializer,
    AbsenceSerializer,
    ExceptionApprovalSerializer,
    NotificationSerializer,
    RvdSerializer,
    ScheduleApprovalSerializer,
    ScheduleEntrySerializer,
    ValidateRequestSerializer,
    ValidationRuleSerializer,
    ViolationSerializer,
)


def _violations_payload(result):
    return ViolationSerializer([v.__dict__ for v in result.violations], many=True).data


def _year_param(request):
    """Год из ?year= или тела запроса (по умолчанию — текущий)."""
    return parse_year(request.query_params.get("year") or request.data.get("year"))


# Записи не редактируются: смена дат — это удаление и новая запись, которая
# проходит все проверки заново. PATCH в обход проверок оставлял бы лазейку.
NO_UPDATE = ["get", "post", "delete", "head", "options"]


class ValidationRuleViewSet(viewsets.ModelViewSet):
    """Пороги правил читает и интерфейс — чтобы не зашивать 28/14/7 в клиент."""

    queryset = ValidationRule.objects.all()
    serializer_class = ValidationRuleSerializer
    http_method_names = ["get", "patch", "head", "options"]

    permission_classes = ADMIN_WRITES


class ScheduleEntryViewSet(viewsets.ModelViewSet):
    serializer_class = ScheduleEntrySerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        # Видимость определяется на сервере, а не фильтром на клиенте.
        visible = self.request.user.visible_employee_ids()
        queryset = ScheduleEntry.objects.filter(employee_id__in=visible).select_related("employee")

        employee_id = self.request.query_params.get("employee_id")
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        tag_id = self.request.query_params.get("tag_id")
        if tag_id:
            queryset = queryset.filter(employee__employee_tags__tag_id=tag_id)
        record_type = self.request.query_params.get("record_type")
        if record_type:
            queryset = queryset.filter(record_type=record_type)
        date_from = self.request.query_params.get("date_from")
        if date_from:
            queryset = queryset.filter(date_from__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            queryset = queryset.filter(date_from__lte=date_to)
        return queryset.distinct()

    @action(detail=False, methods=["post"])
    def validate(self, request):
        """Предварительная проверка — интерфейс показывает проблему до сохранения."""
        payload = ValidateRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        result = calculations.validate_entry(
            request.user, data["date_from"], data["date_to"], data["record_type"]
        )
        new_end, added = calculations.extend_for_holidays(data["date_from"], data["date_to"])

        return Response({
            "is_valid": result.is_valid,
            "violations": _violations_payload(result),
            "vacation_days": calculations.vacation_days(data["date_from"], data["date_to"]),
            "calendar_days": (data["date_to"] - data["date_from"]).days + 1,
            "holiday_extension": {
                "suggested_date_to": new_end,
                "added_days": added,
            },
        })

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        employee = data.get("employee") or request.user
        if employee != request.user and not request.user.is_admin:
            return Response(
                {"detail": "Нельзя вносить график за другого сотрудника"},
                status=status.HTTP_403_FORBIDDEN,
            )

        date_to = data.get("date_to") or data["date_from"]
        result = calculations.validate_entry(
            employee, data["date_from"], date_to, data["record_type"]
        )

        if any(not v.can_be_approved for v in result.violations):
            # Пересечение с собственным отпуском подтвердить нельзя —
            # такую запись не сохраняем вовсе.
            return Response(
                {
                    "detail": "Период нельзя сохранить — выберите другие даты",
                    "code": "validation_blocked",
                    "violations": _violations_payload(result),
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Нарушение, которое руководитель может подтвердить, не мешает
        # сохранить черновик: иначе подтверждать было бы нечего. Черновик
        # не занимает места в лимитах коллег, а отправить график с ним нельзя,
        # пока руководитель не подтвердит исключение (approvals.submit_year).
        entry = serializer.save(employee=employee, created_by=request.user)
        if result.violations:
            approvals.request_exception(entry, result.violations)
        return Response(self.get_serializer(entry).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        """Смена дат черновика — через те же проверки, что и создание.

        Раньше PATCH сохранял любые поля без проверок, включая статус.
        Утверждённый отпуск меняется только через актуализацию графика
        (amendments): открытие правок возвращает записи в черновик.
        """
        entry = self.get_object()
        if entry.employee_id != request.user.id and not request.user.is_admin:
            return Response({"detail": "Изменить запись может только её владелец"},
                            status=status.HTTP_403_FORBIDDEN)
        if entry.status not in (ScheduleEntry.Status.DRAFT, ScheduleEntry.Status.REJECTED):
            return Response(
                {"detail": "Отправленный или утверждённый отпуск нельзя изменить: "
                           "отзовите график или откройте его для правок",
                 "code": "entry_locked"},
                status=status.HTTP_409_CONFLICT)
        unknown = set(request.data) - {"date_from", "date_to"}
        if unknown:
            return Response({"detail": "Изменить можно только даты",
                             "fields": sorted(unknown)}, status=status.HTTP_400_BAD_REQUEST)
        payload = ValidateRequestSerializer(data={
            "date_from": request.data.get("date_from", entry.date_from),
            "date_to": request.data.get("date_to", entry.effective_date_to),
            "record_type": entry.record_type})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        result = calculations.validate_entry(
            entry.employee, data["date_from"], data["date_to"], entry.record_type,
            exclude_entry_id=entry.id)
        if any(not v.can_be_approved for v in result.violations):
            return Response({"detail": "Период нельзя сохранить — выберите другие даты",
                             "code": "validation_blocked",
                             "violations": _violations_payload(result)},
                            status=status.HTTP_409_CONFLICT)
        with transaction.atomic():
            entry.date_from, entry.date_to = data["date_from"], data["date_to"]
            entry.status = ScheduleEntry.Status.DRAFT  # отклонённый после правки — снова черновик
            entry.rejection_comment = ""
            entry.save()
            if result.violations:
                approvals.request_exception(entry, result.violations)
        return Response(self.get_serializer(entry).data)

    def update(self, request, *args, **kwargs):
        return Response({"detail": "Используйте PATCH с полями date_from и date_to"},
                        status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        entry = self.get_object()
        # Видимость шире прав: руководитель видит записи подчинённых,
        # но удалять их не вправе.
        if entry.employee_id != request.user.id and not request.user.is_admin:
            return Response({"detail": "Удалить запись может только её владелец"},
                            status=status.HTTP_403_FORBIDDEN)
        if entry.status not in (ScheduleEntry.Status.DRAFT, ScheduleEntry.Status.REJECTED):
            return Response(
                {"detail": "Отправленный или утверждённый отпуск нельзя удалить: "
                           "отзовите график или откройте его для правок",
                 "code": "entry_locked"},
                status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by("-created_at")

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """Счётчик в шапке: считается по всем, а не по загруженной странице."""
        return Response({"count": self.get_queryset().filter(is_read=False).count()})

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(is_read=False).update(is_read=True)
        return Response({"updated": updated})

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        return Response({"status": "ok"})


class BalanceView(APIView):
    def get(self, request, employee_id):
        employee = visible_employee_or_403(request.user, employee_id)
        on_date = request.query_params.get("date")
        target = parse_date(on_date) if on_date else date.today()
        balance = calculations.balance_on(employee, target)
        return Response({
            "employee_id": str(employee.id),
            "date": target,
            "balance_days": float(balance),
            "balance_days_rounded": calculations.round_days(balance),
        })


class ScheduleApprovalViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ScheduleApprovalSerializer

    def get_queryset(self):
        user = self.request.user
        visible = user.visible_employee_ids()
        queryset = ScheduleApproval.objects.filter(
            Q(employee_id__in=visible) | Q(steps__approver=user)
        ).distinct().select_related("employee").prefetch_related("steps")

        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    @action(detail=True, methods=["post"], url_path="steps/(?P<step_id>[^/.]+)/approve")
    def approve_step(self, request, pk=None, step_id=None):
        step = get_object_or_404(ApprovalStep, id=step_id, approval_id=pk)
        try:
            approval = approvals.decide_step(
                step, request.user, approved=True,
                comment=request.data.get("comment", ""))
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "approval_error"},
                            status=status.HTTP_409_CONFLICT)
        return Response(ScheduleApprovalSerializer(approval).data)

    @action(detail=True, methods=["post"], url_path="steps/(?P<step_id>[^/.]+)/reject")
    def reject_step(self, request, pk=None, step_id=None):
        step = get_object_or_404(ApprovalStep, id=step_id, approval_id=pk)
        try:
            approval = approvals.decide_step(
                step, request.user, approved=False,
                comment=request.data.get("comment", ""))
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "approval_error"},
                            status=status.HTTP_409_CONFLICT)
        return Response(ScheduleApprovalSerializer(approval).data)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        approval = self.get_object()
        try:
            approvals.withdraw(approval, request.user)
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "approval_error"},
                            status=status.HTTP_409_CONFLICT)
        return Response(ScheduleApprovalSerializer(approval).data)


class SubmitYearView(APIView):
    def post(self, request, employee_id):
        employee = get_object_or_404(Employee, id=employee_id)
        if employee != request.user and not request.user.is_admin:
            return Response({"detail": "Нельзя отправить чужой график"},
                            status=status.HTTP_403_FORBIDDEN)
        year = _year_param(request)
        try:
            approval = approvals.submit_year(employee, year, request.user)
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "submit_blocked"},
                            status=status.HTTP_409_CONFLICT)
        return Response(ScheduleApprovalSerializer(approval).data,
                        status=status.HTTP_201_CREATED)


class EntryExceptionView(APIView):
    def get(self, request, entry_id):
        entry = get_object_or_404(ScheduleEntry, id=entry_id)
        if entry.employee_id not in request.user.visible_employee_ids():
            return Response({"detail": "Нет доступа"}, status=status.HTTP_403_FORBIDDEN)
        return Response(ExceptionApprovalSerializer(
            entry.exception_approvals.all(), many=True).data)

    def post(self, request, entry_id):
        entry = get_object_or_404(ScheduleEntry, id=entry_id)
        try:
            approval = approvals.approve_exception(
                entry, request.data.get("rule_code"),
                request.user, request.data.get("comment", ""))
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "exception_error"},
                            status=status.HTTP_409_CONFLICT)

        remaining = approvals.unresolved_violations(entry)
        if not remaining and entry.status == ScheduleEntry.Status.DRAFT:
            entry.save()
        return Response({
            "approval": ExceptionApprovalSerializer(approval).data,
            "remaining_violations": _violations_payload(
                type("R", (), {"violations": remaining})()),
        }, status=status.HTTP_201_CREATED)


class AnnualSummaryView(APIView):
    """Показатели готовности графика — норма, непрерывность, расход накопленных."""

    def get(self, request, employee_id):
        employee = visible_employee_or_403(request.user, employee_id)
        year = _year_param(request)

        summary = calculations.annual_summary(employee, year)
        summary["tags"] = calculations.tag_availability(employee, year)

        amendment = approvals.active_amendment(employee, year)
        approval = ScheduleApproval.objects.filter(
            employee=employee, year=year,
            status=ScheduleApproval.Status.PENDING).first()
        summary["submitted"] = bool(approval)
        summary["approval_id"] = str(approval.id) if approval else None
        # Прогресс цепочки: без него сотрудник не видит, на каком уровне
        # застрял график и кого стоит поторопить.
        summary["approval_steps"] = [
            {
                "order": step.order,
                "level_display": step.get_level_display(),
                "approver_name": step.approver.full_name,
                "status": step.status,
                "comment": step.comment,
            }
            for step in approval.steps.select_related("approver")
        ] if approval else []
        summary["manager_name"] = (
            employee.manager.full_name if employee.manager else None)

        approved = approvals.approved_approval(employee, year)
        summary["year_approved"] = bool(approved) and not amendment
        summary["amendment_id"] = str(amendment.id) if amendment else None
        summary["amendment_status"] = amendment.status if amendment else None
        return Response(summary)


class HolidayListView(APIView):
    def get(self, request):
        year = _year_param(request)
        rows = Holiday.objects.filter(date__year=year)
        return Response([
            {"date": row.date, "name": row.name, "day_type": row.day_type}
            for row in rows
        ])


# Допустимые значения настроек: за пределами — заведомая ошибка ввода,
# а не осознанное решение (норма 600 дней, 0 уровней согласования).
SETTINGS_LIMITS = {
    "annual_vacation_norm_days": (Decimal("1"), Decimal("90")),
    "monthly_accrual_rate": (Decimal("0"), Decimal("10")),
    "approval_levels": (1, 5),
    "workday_hours": (Decimal("1"), Decimal("24")),
}


def _settings_payload(row):
    return {
        "annual_vacation_norm_days": float(row.annual_vacation_norm_days),
        "monthly_accrual_rate": float(row.monthly_accrual_rate),
        "approval_levels": row.approval_levels,
        "workday_hours": float(row.workday_hours),
        "rvd_compensation_tracking_enabled": row.rvd_compensation_tracking_enabled,
    }


class CompanySettingsView(APIView):
    permission_classes = ADMIN_WRITES

    """Глобальные настройки расчётов. Читают все, меняет администратор.

    Изменение действует сразу и на всех: баланс не хранится, а считается,
    поэтому новая ставка начисления пересчитывает остатки задним числом.
    Интерфейс предупреждает об этом перед сохранением.
    """

    def get(self, request):
        return Response(_settings_payload(CompanySettings.get_solo()))

    def patch(self, request):
        row = CompanySettings.get_solo()
        errors = {}
        for field, (low, high) in SETTINGS_LIMITS.items():
            if field not in request.data:
                continue
            raw = request.data[field]
            try:
                value = int(raw) if field == "approval_levels" else Decimal(str(raw))
                if field == "approval_levels" and str(raw).strip() != str(value):
                    raise ValueError
            except (InvalidOperation, ValueError, TypeError):
                errors[field] = ["Ожидается число"]
                continue
            if not low <= value <= high:
                errors[field] = [f"Допустимо от {low} до {high}"]
                continue
            setattr(row, field, value)
        flag = request.data.get("rvd_compensation_tracking_enabled")
        if flag is not None:
            if not isinstance(flag, bool):
                errors["rvd_compensation_tracking_enabled"] = ["Ожидается да или нет"]
            else:
                row.rvd_compensation_tracking_enabled = flag
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        row.save()
        return Response(_settings_payload(row))


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first, second):
    """Контрастность по WCAG 2.1: от 1 (одинаковые) до 21 (чёрный на белом)."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class BrandingView(APIView):
    """Оформление под компанию. Читают все, меняет администратор."""

    permission_classes = ADMIN_WRITES

    def get(self, request):
        row = CompanySettings.get_solo()
        return Response({
            "organization_name": row.organization_name,
            "brand_primary_color": row.brand_primary_color,
            "brand_contrast_color": row.brand_contrast_color,
            "brand_radius": row.brand_radius,
            "brand_font_family": row.brand_font_family,
            "brand_logo": row.brand_logo.url if row.brand_logo else None,
        })

    def patch(self, request):
        row = CompanySettings.get_solo()
        allowed = ["organization_name", "brand_primary_color", "brand_contrast_color",
                   "brand_radius", "brand_font_family"]
        colour = request.data.get("brand_primary_color")
        if colour and not re.fullmatch(r"#[0-9A-Fa-f]{6}", colour):
            return Response({"errors": {"brand_primary_color": ["Ожидается цвет вида #RRGGBB"]}},
                            status=status.HTTP_400_BAD_REQUEST)
        text = request.data.get("brand_contrast_color", row.brand_contrast_color) or "#FFFFFF"
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
            return Response({"errors": {"brand_contrast_color": ["Ожидается цвет вида #RRGGBB"]}},
                            status=status.HTTP_400_BAD_REQUEST)
        # Цвет бренда — фон кнопок с текстом: при контрасте ниже 4.5 (WCAG AA)
        # надписи на кнопках плохо читаются (06-ux-branding.md).
        base = colour or row.brand_primary_color
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", base or "") and contrast_ratio(base, text) < 4.5:
            ratio = contrast_ratio(base, text)
            return Response({"errors": {"brand_primary_color": [
                f"Текст на кнопках будет плохо читаться: контраст {ratio:.1f}, "
                "нужно не меньше 4.5. Выберите цвет темнее"]}},
                status=status.HTTP_400_BAD_REQUEST)
        name = request.data.get("organization_name")
        if name is not None and not str(name).strip():
            return Response({"errors": {"organization_name": ["Название не может быть пустым"]}},
                            status=status.HTTP_400_BAD_REQUEST)
        for field in allowed:
            if field in request.data:
                setattr(row, field, request.data[field])
        row.save()
        return self.get(request)


class AmendmentView(APIView):
    """Актуализация утверждённого графика."""

    def post(self, request, employee_id):
        employee = get_object_or_404(Employee, id=employee_id)
        year = _year_param(request)
        try:
            amendment = approvals.open_amendment(employee, year, request.user)
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "amendment_error"},
                            status=status.HTTP_409_CONFLICT)
        return Response(ScheduleApprovalSerializer(amendment).data,
                        status=status.HTTP_201_CREATED)


class AmendmentActionView(APIView):
    def post(self, request, approval_id, action_name):
        amendment = get_object_or_404(ScheduleApproval, id=approval_id)
        handler = {
            "submit": approvals.submit_amendment,
            "cancel": approvals.cancel_amendment,
        }.get(action_name)
        if handler is None:
            return Response({"detail": "Неизвестное действие"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            handler(amendment, request.user)
        except approvals.ApprovalError as error:
            return Response({"detail": str(error), "code": "amendment_error"},
                            status=status.HTTP_409_CONFLICT)
        amendment.refresh_from_db()
        return Response(ScheduleApprovalSerializer(amendment).data)


class RvdAdminViewSet(viewsets.ModelViewSet):
    """Ввод работы в выходные дни администратором.

    По принятому процессу администратор в начале месяца проставляет РВД
    за прошедший месяц, поэтому отдельного согласования РВД нет:
    запись администратора и есть подтверждение.
    """

    serializer_class = RvdSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return super().get_permissions()
        return [permission() for permission in ADMIN_ONLY]

    def get_queryset(self):
        queryset = ScheduleEntry.objects.filter(
            record_type=ScheduleEntry.RecordType.RVD).select_related("employee")
        if not self.request.user.is_admin:
            queryset = queryset.filter(
                employee_id__in=self.request.user.visible_employee_ids())

        params = self.request.query_params
        if params.get("employee_id"):
            queryset = queryset.filter(employee_id=params["employee_id"])
        if params.get("month") and params.get("year"):
            queryset = queryset.filter(
                date_from__year=params["year"], date_from__month=params["month"])
        return queryset.order_by("-date_from")

    def perform_create(self, serializer):
        entry = serializer.save(
            created_by=self.request.user,
            record_type=ScheduleEntry.RecordType.RVD,
            status=ScheduleEntry.Status.APPROVED)
        calculations.sync_rvd_accrual(entry)

    def perform_update(self, serializer):
        entry = serializer.save()
        calculations.sync_rvd_accrual(entry)

    @action(detail=False, methods=["get"], url_path="tracking")
    def tracking(self, request):
        """Ведётся ли учёт компенсации для сотрудника и чья настройка действует —
        форма ввода скрывает выбор компенсации, когда учёт выключен."""
        employee = get_object_or_404(Employee, id=request.query_params.get("employee_id"))
        source = "компания"
        if employee.rvd_compensation_tracking_override is not None:
            source = "сотрудник"
        else:
            unit = employee.org_unit
            while unit is not None:
                if unit.rvd_compensation_tracking_override is not None:
                    source = unit.name
                    break
                unit = unit.parent
        return Response({"tracked": calculations.rvd_compensation_tracked(employee),
                         "source": source})

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        """Ввод РВД сразу по нескольким сотрудникам за месяц."""
        rows = request.data.get("rows")
        if not isinstance(rows, list) or not rows:
            return Response({"detail": "Не передан список записей"},
                            status=status.HTTP_400_BAD_REQUEST)

        serializer = RvdSerializer(data=rows, many=True)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors},
                            status=status.HTTP_400_BAD_REQUEST)

        created = []
        with transaction.atomic():
            for item in serializer.validated_data:
                entry = ScheduleEntry.objects.create(
                    **item,
                    record_type=ScheduleEntry.RecordType.RVD,
                    status=ScheduleEntry.Status.APPROVED,
                    created_by=request.user)
                calculations.sync_rvd_accrual(entry)
                created.append(entry)

        return Response(RvdSerializer(created, many=True).data,
                        status=status.HTTP_201_CREATED)


class TimeOffBalanceView(APIView):
    def get(self, request, employee_id):
        employee = visible_employee_or_403(request.user, employee_id)

        summary = calculations.time_off_summary(employee)
        # Отгулы на согласовании часы ещё не списали, но новый отгул под них
        # уже не дадут (validate_day_off): показываем, сколько можно попросить.
        pending = ScheduleEntry.objects.filter(
            employee=employee, record_type=ScheduleEntry.RecordType.DAY_OFF,
            status=ScheduleEntry.Status.PENDING_APPROVAL).count()
        summary["pending_days"] = pending
        summary["free_days"] = max(summary["available_days"] - pending, 0)
        summary["history"] = [
            {
                "date": row.entry_date,
                "hours": float(row.hours_delta),
                "reason": row.get_reason_display(),
                "comment": row.comment,
            }
            for row in employee.time_off_entries.all()
        ]
        return Response(summary)


class DayOffViewSet(viewsets.ModelViewSet):
    """Отгулы сотрудника."""

    serializer_class = ScheduleEntrySerializer
    http_method_names = NO_UPDATE

    def get_queryset(self):
        queryset = ScheduleEntry.objects.filter(
            record_type=ScheduleEntry.RecordType.DAY_OFF,
            employee_id__in=self.request.user.visible_employee_ids(),
        ).select_related("employee")
        employee_id = self.request.query_params.get("employee_id")
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        return queryset.order_by("-date_from")

    @action(detail=False, methods=["post"])
    def validate(self, request):
        target = request.data.get("date")
        if not target:
            return Response({"detail": "Не указана дата"},
                            status=status.HTTP_400_BAD_REQUEST)
        result = calculations.validate_day_off(request.user, parse_date(target))
        return Response({
            "is_valid": result.is_valid,
            "violations": _violations_payload(result),
            "balance": calculations.time_off_summary(request.user),
        })

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        target = request.data.get("date_from") or request.data.get("date")
        if not target:
            return Response({"detail": "Не указана дата"},
                            status=status.HTTP_400_BAD_REQUEST)
        target_date = parse_date(target, "date_from")

        result = calculations.validate_day_off(request.user, target_date)
        if not result.is_valid:
            return Response({
                "detail": "Отгул нельзя оформить на эту дату",
                "code": "day_off_blocked",
                "violations": _violations_payload(result),
            }, status=status.HTTP_409_CONFLICT)

        entry = ScheduleEntry.objects.create(
            employee=request.user, created_by=request.user,
            record_type=ScheduleEntry.RecordType.DAY_OFF,
            date_from=target_date, date_to=target_date,
            status=ScheduleEntry.Status.PENDING_APPROVAL)

        manager = request.user.manager
        if manager:
            approvals.notify(
                manager, Notification.NotificationType.APPROVAL_REQUIRED,
                f"{request.user.full_name} просит отгул на {target_date}", entry)

        return Response(self.get_serializer(entry).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        entry = self.get_object()
        manager = entry.employee.manager
        if not (request.user.is_admin or (manager and request.user.id == manager.id)):
            return Response({"detail": "Отгул согласует непосредственный руководитель"},
                            status=status.HTTP_403_FORBIDDEN)
        if entry.status != ScheduleEntry.Status.PENDING_APPROVAL:
            return Response({"detail": "Решение по отгулу уже принято"},
                            status=status.HTTP_409_CONFLICT)

        with transaction.atomic():
            entry.status = ScheduleEntry.Status.APPROVED
            entry.save(update_fields=["status"])
            # Часы списываются в момент согласования, а не заявки:
            # отклонённый отгул не должен уменьшать баланс.
            calculations.spend_day_off(entry)

        approvals.notify(entry.employee,
                         Notification.NotificationType.APPROVAL_DECIDED,
                         f"Отгул на {entry.date_from} согласован", entry)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        entry = self.get_object()
        manager = entry.employee.manager
        if not (request.user.is_admin or (manager and request.user.id == manager.id)):
            return Response({"detail": "Отгул согласует непосредственный руководитель"},
                            status=status.HTTP_403_FORBIDDEN)
        comment = (request.data.get("comment") or "").strip()
        if not comment:
            return Response({"detail": "Комментарий обязателен при отклонении"},
                            status=status.HTTP_409_CONFLICT)

        entry.status = ScheduleEntry.Status.REJECTED
        entry.save(update_fields=["status"])
        approvals.notify(entry.employee,
                         Notification.NotificationType.APPROVAL_DECIDED,
                         f"Отгул на {entry.date_from} отклонён: {comment}", entry)
        return Response(self.get_serializer(entry).data)

    def destroy(self, request, *args, **kwargs):
        entry = self.get_object()
        if request.user.is_admin:
            return super().destroy(request, *args, **kwargs)  # регистр очистится каскадом
        if entry.employee_id != request.user.id:
            return Response({"detail": "Отменить отгул может только сотрудник"},
                            status=status.HTTP_403_FORBIDDEN)
        # Согласованный отгул сотрудник не отменяет сам: удаление вернуло бы
        # списанные часы задним числом. Отмена — через администратора.
        if entry.status not in (ScheduleEntry.Status.PENDING_APPROVAL,
                                ScheduleEntry.Status.REJECTED):
            return Response({"detail": "Согласованный отгул отменяет администратор",
                             "code": "day_off_locked"},
                            status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)


class AbsenceReasonViewSet(viewsets.ModelViewSet):
    """Справочник причин отсутствия. Читают все, правит администратор."""

    serializer_class = AbsenceReasonSerializer
    queryset = AbsenceReason.objects.all().order_by("name")

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return super().get_permissions()
        return [permission() for permission in ADMIN_ONLY]


class AbsenceViewSet(viewsets.ModelViewSet):
    """Внеплановые отсутствия.

    Фиксируются постфактум. Сотрудник вносит своё отсутствие,
    руководитель — по своей структуре, администратор — по всем.
    Отдельного согласования нет: это фиксация факта, а не заявка.
    """

    serializer_class = AbsenceSerializer
    http_method_names = NO_UPDATE

    def get_queryset(self):
        queryset = ScheduleEntry.objects.filter(
            record_type=ScheduleEntry.RecordType.ABSENCE,
            employee_id__in=self.request.user.visible_employee_ids(),
        ).select_related("employee", "reason", "created_by")

        params = self.request.query_params
        if params.get("employee_id"):
            queryset = queryset.filter(employee_id=params["employee_id"])
        if params.get("year"):
            queryset = queryset.filter(date_from__year=params["year"])
        return queryset.order_by("-date_from")

    def _can_record_for(self, employee):
        user = self.request.user
        if user.is_admin or user.id == employee.id:
            return True
        return employee.id in user.visible_employee_ids()

    def _validate(self, data, exclude_entry_id=None):
        employee = data.get("employee") or self.request.user
        date_to = data.get("date_to") or data["date_from"]
        return employee, date_to, calculations.validate_absence(
            employee, data["date_from"], date_to,
            data.get("reason"), data.get("custom_reason_text", ""),
            exclude_entry_id=exclude_entry_id)

    @action(detail=False, methods=["post"])
    def validate(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _, date_to, result = self._validate(serializer.validated_data)
        return Response({
            "is_valid": result.is_valid,
            "violations": _violations_payload(result),
            "days": calculations.absence_days(
                serializer.validated_data["date_from"], date_to),
        })

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee, date_to, result = self._validate(serializer.validated_data)

        if not self._can_record_for(employee):
            return Response({"detail": "Нельзя вносить отсутствие этого сотрудника"},
                            status=status.HTTP_403_FORBIDDEN)

        if not result.is_valid:
            return Response({
                "detail": "Отсутствие нельзя зафиксировать",
                "code": "absence_blocked",
                "violations": _violations_payload(result),
            }, status=status.HTTP_409_CONFLICT)

        entry = serializer.save(
            employee=employee, created_by=request.user, date_to=date_to,
            record_type=ScheduleEntry.RecordType.ABSENCE,
            status=ScheduleEntry.Status.APPROVED)

        manager = employee.manager
        if manager and manager.id != request.user.id:
            approvals.notify(
                manager, Notification.NotificationType.APPROVAL_DECIDED,
                f"{employee.full_name}: отсутствие "
                f"{entry.date_from}—{entry.effective_date_to} "
                f"({entry.reason.name if entry.reason else 'без причины'})", entry)

        return Response(self.get_serializer(entry).data,
                        status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        entry = self.get_object()
        if not (request.user.is_admin or entry.created_by_id == request.user.id):
            return Response(
                {"detail": "Удалить запись может её автор или администратор"},
                status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)


class AbsenceSummaryView(APIView):
    """Отдельный счётчик отсутствий, не влияющий на баланс отпуска."""

    def get(self, request, employee_id):
        employee = visible_employee_or_403(request.user, employee_id)
        year = _year_param(request)
        return Response(calculations.absence_summary(employee, year))


class RvdTrackingOverridesView(APIView):
    """Исключения из общей настройки учёта компенсации РВД (ФЗ, раздел РВД).

    GET — подразделения (все, для выбора) и действующие исключения сотрудников.
    POST {kind: "unit"|"employee", id, value: true|false|null} — задать или
    снять исключение; null — «как выше по структуре».
    """

    permission_classes = ADMIN_ONLY

    def get(self, request):
        from employees.models import OrgUnit

        def path(unit):
            names = []
            while unit is not None:
                names.append(unit.name)
                unit = unit.parent
            return " / ".join(reversed(names))

        units = OrgUnit.objects.select_related("parent").all()
        return Response({
            "company": CompanySettings.get_solo().rvd_compensation_tracking_enabled,
            "units": sorted(({"id": str(u.id), "name": u.name, "path": path(u),
                              "value": u.rvd_compensation_tracking_override} for u in units),
                            key=lambda r: r["path"]),
            "employees": [{"id": str(e.id), "name": e.full_name,
                           "org_unit": e.org_unit.name if e.org_unit else None,
                           "value": e.rvd_compensation_tracking_override}
                          for e in Employee.objects.filter(
                              rvd_compensation_tracking_override__isnull=False)
                          .select_related("org_unit").order_by("full_name")],
        })

    def post(self, request):
        from employees.models import OrgUnit

        kind, value = request.data.get("kind"), request.data.get("value")
        if value is not None and not isinstance(value, bool):
            return Response({"detail": "Значение: да, нет или как выше"},
                            status=status.HTTP_400_BAD_REQUEST)
        model = {"unit": OrgUnit, "employee": Employee}.get(kind)
        if model is None:
            return Response({"detail": "Укажите подразделение или сотрудника"},
                            status=status.HTTP_400_BAD_REQUEST)
        target = get_object_or_404(model, id=request.data.get("id"))
        target.rvd_compensation_tracking_override = value
        target.save(update_fields=["rvd_compensation_tracking_override"])
        return self.get(request)
