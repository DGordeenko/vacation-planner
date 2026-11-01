from rest_framework import serializers

from company.models import AbsenceReason
from employees.models import Employee
from schedule.models import (
    ApprovalStep,
    ExceptionApproval,
    Notification,
    ScheduleApproval,
    ScheduleEntry,
    ValidationRule,
)
from schedule.validators import TaskLinkField


class ValidationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidationRule
        fields = ["code", "name", "description", "is_enabled", "threshold_value", "approver_level"]
        read_only_fields = ["code", "name", "description"]

    def validate_threshold_value(self, value):
        # У правил вроде «захват выходных» порога нет: число там ничего
        # не значит, а пустой порог у правила с порогом выключил бы проверку.
        had_threshold = self.instance is not None and self.instance.threshold_value is not None
        if self.instance is not None and not had_threshold and value is not None:
            raise serializers.ValidationError("У этого правила нет порога")
        if had_threshold and value is None:
            raise serializers.ValidationError(
                "Порог обязателен. Чтобы не проверять правило, выключите его")
        if value is not None and value < 0:
            raise serializers.ValidationError("Порог не может быть отрицательным")
        return value


class ColleagueOnLeaveSerializer(serializers.Serializer):
    name = serializers.CharField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()


class ViolationSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    message = serializers.CharField()
    can_be_approved = serializers.BooleanField()
    colleagues = ColleagueOnLeaveSerializer(many=True, default=list)


class ScheduleEntrySerializer(serializers.ModelSerializer):
    task_link = TaskLinkField()
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    # По умолчанию запись создаётся себе; администратор может указать сотрудника явно.
    employee = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.all(), required=False
    )
    vacation_days = serializers.SerializerMethodField()
    pending_exceptions = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleEntry
        fields = [
            "id", "employee", "employee_name", "record_type", "date_from", "date_to",
            "reason", "custom_reason_text", "task_link", "overtime_hours",
            "rvd_compensation_type", "extended_by_holiday_days", "status",
            "vacation_days", "pending_exceptions", "rejection_comment", "created_at",
        ]
        # Статус меняет только сервер: отправка, согласование, отказ. Иначе
        # сотрудник мог бы сам себе «утвердить» отпуск одним запросом.
        read_only_fields = ["extended_by_holiday_days", "rejection_comment", "created_at",
                            "status"]

    def get_vacation_days(self, obj):
        from schedule.calculations import vacation_days
        if obj.record_type != ScheduleEntry.RecordType.VACATION:
            return None
        return vacation_days(obj.date_from, obj.effective_date_to)

    def get_pending_exceptions(self, obj):
        """Нарушения черновика, которые ещё ждут подтверждения руководителя.

        Пометка вычисляется, а не хранится: правило могло начать или перестать
        срабатывать после сохранения (коллега отправил график, сменился порог).
        Отправленные и утверждённые отпуска уже прошли проверку при отправке.
        """
        if (obj.record_type != ScheduleEntry.RecordType.VACATION
                or obj.status != ScheduleEntry.Status.DRAFT):
            return []
        from schedule.approvals import unresolved_violations
        return ViolationSerializer(
            [v.__dict__ for v in unresolved_violations(obj)], many=True).data


class ValidateRequestSerializer(serializers.Serializer):
    """Предварительная проверка без сохранения."""

    date_from = serializers.DateField()
    date_to = serializers.DateField()
    record_type = serializers.ChoiceField(
        choices=ScheduleEntry.RecordType.choices,
        default=ScheduleEntry.RecordType.VACATION,
    )

    def validate(self, attrs):
        if attrs["date_to"] < attrs["date_from"]:
            raise serializers.ValidationError(
                {"date_to": "Дата окончания раньше даты начала"}
            )
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id", "notification_type", "message", "is_read",
            "related_entity_type", "related_entity_id", "created_at",
        ]


class ApprovalStepSerializer(serializers.ModelSerializer):
    approver_name = serializers.CharField(source="approver.full_name", read_only=True)
    level_display = serializers.CharField(source="get_level_display", read_only=True)

    class Meta:
        model = ApprovalStep
        fields = ["id", "order", "level", "level_display", "approver",
                  "approver_name", "status", "comment", "decided_at"]


class ScheduleApprovalSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    steps = ApprovalStepSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleApproval
        fields = ["id", "employee", "employee_name", "year", "scope",
                  "status", "steps", "created_at"]


class ExceptionApprovalSerializer(serializers.ModelSerializer):
    approved_by_name = serializers.CharField(
        source="approved_by.full_name", read_only=True)
    rule_name = serializers.CharField(source="rule.name", read_only=True)

    class Meta:
        model = ExceptionApproval
        fields = ["id", "schedule_entry", "rule", "rule_name",
                  "approved_by", "approved_by_name", "comment", "approved_at"]


class RvdSerializer(serializers.ModelSerializer):
    # По ФЗ ссылка на задачу у РВД обязательна (решение от 31.10.2026).
    task_link = TaskLinkField(required=True, allow_blank=False, error_messages={
        "required": "Укажите ссылку на задачу", "blank": "Укажите ссылку на задачу"})
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(
        source="employee.employee_number", read_only=True)
    compensation_display = serializers.CharField(
        source="get_rvd_compensation_type_display", read_only=True)

    class Meta:
        model = ScheduleEntry
        fields = ["id", "employee", "employee_name", "employee_number",
                  "date_from", "work_hours", "rvd_compensation_type",
                  "compensation_display", "task_link"]

    def validate_work_hours(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError("Часы должны быть больше нуля")
        if value > 24:
            raise serializers.ValidationError("В сутках не больше 24 часов")
        return value

    rvd_compensation_type = serializers.ChoiceField(
        choices=ScheduleEntry.RvdCompensation.choices, required=False, allow_blank=True)

    def validate(self, attrs):
        from schedule import calculations

        employee = attrs.get("employee") or getattr(self.instance, "employee", None)
        day = attrs.get("date_from") or getattr(self.instance, "date_from", None)
        errors = {}
        # РВД — работа в выходной или праздник; на рабочий день это ошибка
        # ввода (решение от 31.10.2026).
        if day and not calculations.is_rest_day(day):
            errors["date_from"] = (f"{day:%d.%m.%Y} — рабочий день по производственному "
                                   "календарю. РВД вносится на выходной или праздник")
        if employee:
            tracked = calculations.rvd_compensation_tracked(employee)
            compensation = attrs.get("rvd_compensation_type",
                                     getattr(self.instance, "rvd_compensation_type", ""))
            if tracked and not compensation:
                errors["rvd_compensation_type"] = "Укажите вид компенсации"
            if not tracked:
                # Учёт компенсации выключен: вид не хранится, отгул не начисляется.
                attrs["rvd_compensation_type"] = ""
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class AbsenceReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = AbsenceReason
        fields = ["id", "code", "name", "is_other"]


class AbsenceSerializer(serializers.ModelSerializer):
    # По умолчанию отсутствие вносится себе; руководитель и администратор
    # могут указать сотрудника явно.
    employee = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.all(), required=False)
    reason = serializers.PrimaryKeyRelatedField(
        queryset=AbsenceReason.objects.all(), required=False, allow_null=True)
    date_to = serializers.DateField(required=False, allow_null=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    reason_name = serializers.CharField(source="reason.name", read_only=True)
    days = serializers.SerializerMethodField()
    recorded_by = serializers.CharField(source="created_by.full_name", read_only=True)
    # Удалить запись может её автор: интерфейсу нужен идентификатор, а не имя —
    # у однофамильцев имена совпадают.
    recorded_by_id = serializers.UUIDField(source="created_by_id", read_only=True)

    class Meta:
        model = ScheduleEntry
        fields = ["id", "employee", "employee_name", "date_from", "date_to",
                  "reason", "reason_name", "custom_reason_text", "days",
                  "recorded_by", "recorded_by_id", "created_at"]

    def get_days(self, obj):
        from schedule.calculations import absence_days
        return absence_days(obj.date_from, obj.effective_date_to)
