from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"
    verbose_name = "Аудит"

    def ready(self):
        from audit import recording
        from company.models import CompanySettings, Holiday
        from employees.models import Employee, EmployeeTag, OrgUnit
        from schedule.models import (
            ApprovalStep, ExceptionApproval, ScheduleApproval,
            ScheduleEntry, ValidationRule, VacationLedger,
        )

        # Что отслеживаем и как это подписывать в журнале.
        recording.track(
            ScheduleEntry,
            label=lambda o: f"{o.get_record_type_display()} "
                            f"{o.date_from}—{o.effective_date_to}",
            subject=lambda o: o.employee)
        recording.track(
            ExceptionApproval,
            label=lambda o: f"Исключение по правилу «{o.rule.name}»",
            subject=lambda o: o.schedule_entry.employee)
        recording.track(
            ScheduleApproval,
            label=lambda o: f"График {o.year}",
            subject=lambda o: o.employee)
        recording.track(
            ApprovalStep,
            label=lambda o: f"Шаг согласования {o.get_level_display()}",
            subject=lambda o: o.approval.employee)
        recording.track(
            VacationLedger,
            label=lambda o: f"{o.get_reason_display()} {o.days_delta} дн.",
            subject=lambda o: o.employee)
        recording.track(
            EmployeeTag,
            label=lambda o: f"Тег «{o.tag.name}»",
            subject=lambda o: o.employee)
        recording.track(
            Employee,
            label=lambda o: f"{o.full_name} ({o.employee_number})",
            subject=lambda o: o)
        recording.track(OrgUnit, label=lambda o: o.name)
        recording.track(Holiday, label=lambda o: f"{o.date} {o.name}")
        recording.track(ValidationRule, label=lambda o: o.name)
        recording.track(CompanySettings, label=lambda o: "Настройки компании")
