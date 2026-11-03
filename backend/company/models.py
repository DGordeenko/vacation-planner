import uuid

from django.core.exceptions import ValidationError
from django.db import models


class Holiday(models.Model):
    """Производственный календарь. Загружается администратором из CSV."""

    class DayType(models.TextChoices):
        HOLIDAY = "holiday", "Праздничный нерабочий"
        PRE_HOLIDAY_SHORT = "pre_holiday_short", "Сокращённый предпраздничный"
        TRANSFERRED_WORKDAY = "transferred_workday", "Перенесённый рабочий"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField(unique=True)
    name = models.CharField(max_length=255)
    day_type = models.CharField(max_length=32, choices=DayType.choices)

    class Meta:
        ordering = ["date"]
        verbose_name = "День календаря"
        verbose_name_plural = "Календарь праздников"

    def __str__(self):
        return f"{self.date} — {self.name}"


class AbsenceReason(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    is_other = models.BooleanField(
        default=False, help_text="Требует заполнения текстовой причины"
    )

    class Meta:
        verbose_name = "Причина отсутствия"
        verbose_name_plural = "Причины отсутствия"

    def __str__(self):
        return self.name


class CompanySettings(models.Model):
    """Единственная строка глобальных настроек (singleton)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    annual_vacation_norm_days = models.DecimalField(
        max_digits=5, decimal_places=2, default=28
    )
    monthly_accrual_rate = models.DecimalField(max_digits=5, decimal_places=2, default=2.33)
    balance_overrun_alert_threshold_days = models.PositiveIntegerField(default=7)
    approval_levels = models.PositiveIntegerField(
        default=4,
        help_text="Сколько уровней руководства согласуют график. "
                  "Если уровней в структуре меньше, цепочка завершается "
                  "администратором.")
    rvd_compensation_tracking_enabled = models.BooleanField(default=True)
    workday_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=8,
        help_text="Длина рабочего дня. Столько часов списывает один отгул.")
    # Пределы сверхурочной работы (ТК РФ, ст. 99): 4 часа за два дня подряд
    # и 120 часов в год. Контроль по умолчанию выключен; при превышении —
    # предупреждение, а не запрет: решение остаётся за руководителем.
    overtime_limits_enabled = models.BooleanField(default=False)
    overtime_limit_two_days_hours = models.DecimalField(
        max_digits=5, decimal_places=2, default=4)
    overtime_limit_year_hours = models.DecimalField(
        max_digits=6, decimal_places=2, default=120)

    organization_name = models.CharField(max_length=255, default="Организация")
    brand_primary_color = models.CharField(max_length=9, default="#2F6FEB")
    brand_contrast_color = models.CharField(max_length=9, blank=True, default="")
    brand_radius = models.CharField(max_length=16, default="8px")
    brand_font_family = models.CharField(max_length=255, default="system-ui, sans-serif")
    brand_logo = models.FileField(upload_to="branding/", null=True, blank=True)
    brand_favicon = models.FileField(upload_to="branding/", null=True, blank=True)

    class Meta:
        verbose_name = "Настройки компании"
        verbose_name_plural = "Настройки компании"

    def __str__(self):
        return "Настройки компании"

    def save(self, *args, **kwargs):
        if not self.pk and CompanySettings.objects.exists():
            raise ValidationError("Настройки компании существуют в единственном экземпляре")
        return super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        settings_row = cls.objects.first()
        if settings_row is None:
            settings_row = cls.objects.create()
        return settings_row


class SettingVersion(models.Model):
    """Значение настройки, действующее с первого числа месяца (ФЗ, вопрос 5).

    Без истории правка нормы или ставки пересчитывала прошлые годы по новому
    значению. Теперь значение берётся на нужный месяц: что было до даты,
    считается по-старому. Логика — в company/history.py.
    """

    class Key(models.TextChoices):
        VACATION_NORM = "annual_vacation_norm_days", "Право на отпуск в год, дн."
        ACCRUAL_RATE = "monthly_accrual_rate", "Начисление в месяц, дн."
        WORKDAY_HOURS = "workday_hours", "Длина рабочего дня, ч"
        RULE_THRESHOLD = "rule_threshold", "Порог правила"
        EMPLOYEE_NORM = "employee_norm", "Своя норма отпуска, дн."

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=32, choices=Key.choices)
    rule_code = models.CharField(max_length=64, blank=True)
    employee = models.ForeignKey("employees.Employee", null=True, blank=True,
                                 on_delete=models.CASCADE, related_name="setting_versions")
    effective_from = models.DateField(help_text="Всегда первое число месяца.")
    # Пусто — «как в общей настройке» (только для своей нормы сотрудника).
    value = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Одно значение на месяц обеспечивает history.set_value (update_or_create):
        # уникальность с пустым сотрудником в БД зависит от версии PostgreSQL.
        ordering = ["key", "rule_code", "effective_from"]
        indexes = [models.Index(fields=["key", "rule_code", "employee", "effective_from"])]
        verbose_name = "Значение настройки с даты"
        verbose_name_plural = "История настроек"

    def __str__(self):
        return f"{self.get_key_display()} с {self.effective_from:%m.%Y}: {self.value}"
