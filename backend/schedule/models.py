import uuid

from django.db import models

from employees.models import Employee


class ValidationRule(models.Model):
    """Справочник правил проверки.

    Пороги и признак включённости — данные, а не код: изменение нормы
    с 28 на 30 дней должно быть настройкой, а не доработкой с миграцией.
    """

    class ApproverLevel(models.TextChoices):
        DIRECT_MANAGER = "direct_manager", "Непосредственный руководитель"
        EMPLOYEE = "employee", "Сам сотрудник"

    code = models.SlugField(max_length=64, primary_key=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=True)
    threshold_value = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    approver_level = models.CharField(
        max_length=32, choices=ApproverLevel.choices, default=ApproverLevel.DIRECT_MANAGER
    )

    class Meta:
        verbose_name = "Правило проверки"
        verbose_name_plural = "Правила проверки"

    def __str__(self):
        return f"{self.name} ({self.code})"


class ScheduleEntry(models.Model):
    """Универсальная запись графика: отпуск, отсутствие, РВД, сверхурочные."""

    class RecordType(models.TextChoices):
        VACATION = "vacation", "Отпуск"
        ABSENCE = "absence", "Внеплановое отсутствие"
        RVD = "rvd", "Работа в выходной день"
        OVERTIME = "overtime", "Сверхурочная работа"
        DAY_OFF = "day_off", "Отгул"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PENDING_APPROVAL = "pending_approval", "На согласовании"
        APPROVED = "approved", "Утверждён"
        REJECTED = "rejected", "Отклонён"
        CANCELLED = "cancelled", "Отменён"

    class RvdCompensation(models.TextChoices):
        DOUBLE_PAY = "double_pay", "Двойная оплата"
        PAY_PLUS_DAY_OFF = "pay_plus_day_off", "Оплата + отгул"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="schedule_entries"
    )
    record_type = models.CharField(max_length=16, choices=RecordType.choices)
    date_from = models.DateField()
    date_to = models.DateField(null=True, blank=True)
    reason = models.ForeignKey(
        "company.AbsenceReason", null=True, blank=True, on_delete=models.PROTECT
    )
    custom_reason_text = models.CharField(max_length=500, blank=True)
    # Не URLField: внутренние трекеры вида https://jira/TASK-1 не имеют
    # домена верхнего уровня и не прошли бы стандартную проверку.
    task_link = models.CharField(max_length=500, blank=True)
    overtime_hours = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    rvd_compensation_type = models.CharField(
        max_length=24, choices=RvdCompensation.choices, blank=True,
        help_text="Только для РВД. Отгул начисляется лишь при «оплата + отгул»."
    )
    work_hours = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Отработано часов в выходной день. Может быть меньше полного дня."
    )
    extended_by_holiday_days = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="entries_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Причина отказа руководителя: сотрудник видит её у самого отпуска,
    # а не только в ленте уведомлений, где она уходит из видимых пяти.
    rejection_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["date_from"]
        verbose_name = "Запись графика"
        verbose_name_plural = "Записи графика"

    def __str__(self):
        return f"{self.employee.full_name}: {self.get_record_type_display()} {self.date_from}"

    @property
    def effective_date_to(self):
        return self.date_to or self.date_from

    def overlaps(self, date_from, date_to):
        return self.date_from <= date_to and self.effective_date_to >= date_from


class VacationLedger(models.Model):
    """Регистр начислений и списаний. Баланс не хранится — он вычисляется."""

    class Reason(models.TextChoices):
        MONTHLY_ACCRUAL = "monthly_accrual", "Ежемесячное начисление"
        VACATION_TAKEN = "vacation_taken", "Списание за отпуск"
        MANUAL_ADJUSTMENT = "manual_adjustment", "Ручная корректировка"
        INITIAL_MIGRATION = "initial_migration", "Начальный остаток"
        TERMINATION_SETTLEMENT = "termination_settlement", "Расчёт при увольнении"
        RECALCULATION = "recalculation", "Пересчёт"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="ledger_entries"
    )
    entry_date = models.DateField()
    # Без округления: округление промежуточных значений накапливает
    # ошибку в целые дни за год.
    days_delta = models.DecimalField(max_digits=6, decimal_places=2)
    reason = models.CharField(max_length=32, choices=Reason.choices)
    related_schedule_entry = models.ForeignKey(
        ScheduleEntry, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entry_date"]
        verbose_name = "Запись регистра отпуска"
        verbose_name_plural = "Регистр отпускных дней"


class ExceptionApproval(models.Model):
    """Подтверждение исключения по заблокированному правилу."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule_entry = models.ForeignKey(
        ScheduleEntry, on_delete=models.CASCADE, related_name="exception_approvals"
    )
    rule = models.ForeignKey(ValidationRule, on_delete=models.PROTECT)
    approved_by = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="exceptions_approved"
    )
    comment = models.TextField()
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Подтверждение исключения"
        verbose_name_plural = "Подтверждения исключений"


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        EXCEPTION_REQUIRED = "exception_required", "Требуется подтверждение исключения"
        TAG_LIMIT_ALERT = "tag_limit_alert", "Превышен лимит по тегу"
        APPROVAL_REQUIRED = "approval_required", "Требуется согласование"
        APPROVAL_DECIDED = "approval_decided", "Решение по согласованию"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="notifications"
    )
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    related_entity_type = models.CharField(max_length=64, blank=True)
    related_entity_id = models.UUIDField(null=True, blank=True)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"


class ScheduleApproval(models.Model):
    """Согласование годового графика.

    В релизе 1 цепочка состоит из одного шага — непосредственный
    руководитель. Модель шагов заложена сразу, чтобы добавление
    остальных уровней в релизе 2 не потребовало миграции данных.
    """

    class Scope(models.TextChoices):
        INITIAL = "initial", "Первичное утверждение"
        AMENDMENT = "amendment", "Актуализация"

    class Status(models.TextChoices):
        OPEN = "open", "Открыт для правок"
        PENDING = "pending", "На согласовании"
        APPROVED = "approved", "Согласован"
        REJECTED = "rejected", "Отклонён"
        WITHDRAWN = "withdrawn", "Отозван"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="schedule_approvals"
    )
    year = models.PositiveIntegerField()
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.INITIAL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    # Снимок утверждённого графика на момент открытия правок.
    # Нужен, чтобы отказ руководителя вернул ровно то, что было утверждено,
    # а не оставил сотрудника с наполовину изменённым графиком.
    snapshot = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Согласование графика"
        verbose_name_plural = "Согласования графиков"

    def __str__(self):
        return f"{self.employee.full_name} — график {self.year} ({self.get_status_display()})"


class ApprovalStep(models.Model):
    class Level(models.TextChoices):
        GROUP = "group", "Руководитель группы"
        DEPARTMENT = "department", "Руководитель отдела"
        DIVISION_UNIT = "division_unit", "Руководитель подразделения"
        DIVISION = "division", "Руководитель дивизиона"
        ADMIN = "admin", "Администратор"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    approval = models.ForeignKey(
        ScheduleApproval, on_delete=models.CASCADE, related_name="steps"
    )
    order = models.PositiveIntegerField(default=1)
    level = models.CharField(max_length=24, choices=Level.choices)
    approver = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="approval_steps"
    )
    status = models.CharField(
        max_length=16, choices=ScheduleApproval.Status.choices,
        default=ScheduleApproval.Status.PENDING
    )
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order"]
        verbose_name = "Шаг согласования"
        verbose_name_plural = "Шаги согласования"


class TimeOffLedger(models.Model):
    """Регистр часов отгула.

    Баланс хранится в часах, а не в днях: РВД может быть отработан
    неполный день, и округление до дней при начислении теряло бы часы.
    Списание происходит целым днём — на длину рабочего дня из настроек.
    """

    class Reason(models.TextChoices):
        RVD_EARNED = "rvd_earned", "Начислено за работу в выходной"
        DAY_OFF_TAKEN = "day_off_taken", "Списано за отгул"
        MANUAL_ADJUSTMENT = "manual_adjustment", "Ручная корректировка"
        INITIAL_MIGRATION = "initial_migration", "Начальный остаток"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="time_off_entries"
    )
    entry_date = models.DateField()
    hours_delta = models.DecimalField(max_digits=6, decimal_places=2)
    reason = models.CharField(max_length=32, choices=Reason.choices)
    related_schedule_entry = models.ForeignKey(
        ScheduleEntry, null=True, blank=True, on_delete=models.CASCADE,
        related_name="time_off_records"
    )
    comment = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entry_date"]
        verbose_name = "Запись регистра отгулов"
        verbose_name_plural = "Регистр отгулов"

    def __str__(self):
        return f"{self.employee.full_name}: {self.hours_delta} ч ({self.get_reason_display()})"
