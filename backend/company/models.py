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
