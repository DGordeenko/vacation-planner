import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class OrgUnit(models.Model):
    """Оргединица любого уровня. Дерево строится через parent."""

    class UnitType(models.TextChoices):
        GROUP = "group", "Группа"
        DEPARTMENT = "department", "Отдел"
        DIVISION_UNIT = "division_unit", "Подразделение"
        DIVISION = "division", "Дивизион"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    unit_type = models.CharField(max_length=32, choices=UnitType.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    manager = models.ForeignKey(
        "Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_units"
    )
    rvd_compensation_tracking_override = models.BooleanField(null=True, blank=True)

    class Meta:
        verbose_name = "Оргединица"
        verbose_name_plural = "Оргединицы"

    def __str__(self):
        return f"{self.name} ({self.get_unit_type_display()})"

    def ancestors(self):
        """Цепочка вверх до корня — используется для построения согласования."""
        node, chain = self.parent, []
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain

    def descendant_ids(self):
        """Все оргединицы ниже данной, включая её саму."""
        collected, frontier = {self.id}, [self]
        while frontier:
            current = frontier.pop()
            for child in current.children.all():
                if child.id not in collected:
                    collected.add(child.id)
                    frontier.append(child)
        return collected


class EmployeeManager(BaseUserManager):
    def create_user(self, email, employee_number, full_name, hire_date, password=None, **extra):
        if not email:
            raise ValueError("Email обязателен")
        employee = self.model(
            email=self.normalize_email(email),
            employee_number=employee_number,
            full_name=full_name,
            hire_date=hire_date,
            **extra,
        )
        # Без пароля учётная запись создаётся «ожидающей приглашения»:
        # сотрудник задаёт пароль по ссылке при первом входе.
        if password:
            employee.set_password(password)
        else:
            employee.set_unusable_password()
        employee.save(using=self._db)
        return employee

    def create_superuser(self, email, employee_number, full_name, hire_date, password, **extra):
        extra.setdefault("is_admin", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_staff", True)
        return self.create_user(email, employee_number, full_name, hire_date, password, **extra)


class Employee(AbstractBaseUser, PermissionsMixin):
    class Status(models.TextChoices):
        ACTIVE = "active", "Работает"
        INACTIVE = "inactive", "Уволен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_number = models.CharField(max_length=32, unique=True)
    full_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    hire_date = models.DateField()
    org_unit = models.ForeignKey(
        OrgUnit, null=True, blank=True, on_delete=models.PROTECT, related_name="employees"
    )
    is_admin = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    termination_date = models.DateField(null=True, blank=True)
    annual_vacation_norm_override = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    rvd_compensation_tracking_override = models.BooleanField(null=True, blank=True)
    invitation_token = models.UUIDField(null=True, blank=True, unique=True)
    email_notifications_enabled = models.BooleanField(
        default=True,
        help_text="Получать уведомления на почту. Настраивается сотрудником.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EmployeeManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["employee_number", "full_name", "hire_date"]

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"

    def __str__(self):
        return f"{self.full_name} ({self.employee_number})"

    @property
    def manager(self):
        """Непосредственный руководитель выводится через оргединицу.

        Отдельного поля нет намеренно: дублирование рассинхронизируется
        при первом же переводе сотрудника (см. модель данных, п. 6.5).
        """
        unit = self.org_unit
        while unit is not None:
            if unit.manager_id and unit.manager_id != self.id:
                return unit.manager
            unit = unit.parent
        return None

    def visible_employee_ids(self):
        """Кого этот пользователь вправе видеть.

        Сотрудник — только себя. Руководитель — всю структуру вниз.
        Администратор — всех.
        """
        if self.is_admin:
            return list(Employee.objects.values_list("id", flat=True))

        managed = list(self.managed_units.all())
        if not managed:
            return [self.id]

        unit_ids = set()
        for unit in managed:
            unit_ids |= unit.descendant_ids()
        ids = set(Employee.objects.filter(org_unit_id__in=unit_ids).values_list("id", flat=True))
        ids.add(self.id)
        return list(ids)


class Tag(models.Model):
    class Source(models.TextChoices):
        AUTO_ORG = "auto_org", "По оргструктуре"
        MANUAL = "manual", "Вручную"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)
    max_simultaneous_absence = models.PositiveIntegerField(
        null=True, blank=True, help_text="Пусто — лимит не ограничен"
    )

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"

    def __str__(self):
        return self.name


class EmployeeTag(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="employee_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="employee_tags")
    source = models.CharField(max_length=16, choices=Tag.Source.choices)
    assigned_by = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="tags_assigned"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("employee", "tag")
        verbose_name = "Тег сотрудника"
        verbose_name_plural = "Теги сотрудников"
