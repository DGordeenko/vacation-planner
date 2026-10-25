from rest_framework import serializers

from company.models import AbsenceReason
from employees.models import Employee
from schedule.validators import TaskLinkField
from schedule.models import (
    ApprovalStep,
    ExceptionApproval,
    Notification,
    ScheduleApproval,
    ScheduleEntry,
    ValidationRule,
)


class ValidationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidationRule
        fields = ["code", "name", "description", "is_enabled", "threshold_value", "approver_level"]
        read_only_fields = ["code"]


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
            "vacation_days", "pending_exceptions", "created_at",
        ]
        read_only_fields = ["extended_by_holiday_days", "created_at"]

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
    task_link = TaskLinkField()
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

    def validate_rvd_compensation_type(self, value):
        if not value:
            raise serializers.ValidationError("Укажите тип оплаты")
        return value


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

    class Meta:
        model = ScheduleEntry
        fields = ["id", "employee", "employee_name", "date_from", "date_to",
                  "reason", "reason_name", "custom_reason_text", "days",
                  "recorded_by", "created_at"]

    def get_days(self, obj):
        from schedule.calculations import absence_days
        return absence_days(obj.date_from, obj.effective_date_to)
