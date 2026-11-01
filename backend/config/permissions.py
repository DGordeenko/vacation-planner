"""Общие проверки прав.

Раньше «только администратор» было записано в пяти местах по-разному:
два одинаковых класса с разными сообщениями (у ввода РВД ошибка говорила
про календарь) и ручные проверки в методах. Теперь — в одном месте.
"""

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission

from audit.drf import RecordAuditActor


class IsAdmin(BasePermission):
    message = "Действие доступно только администратору"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.is_admin)


class AdminWritesOthersRead(IsAdmin):
    """Справочники и настройки: читают все вошедшие, меняет администратор."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return super().has_permission(request, view)


# Список для permission_classes: автор изменения фиксируется для журнала
# до проверки прав (audit/drf.py), затем проверяется право.
ADMIN_ONLY = [RecordAuditActor, IsAdmin]
ADMIN_WRITES = [RecordAuditActor, AdminWritesOthersRead]


def visible_employee_or_403(user, employee_id):
    """Сотрудник, если пользователь вправе его видеть (сам, своя структура, админ)."""
    from employees.models import Employee

    if str(employee_id) not in {str(i) for i in user.visible_employee_ids()}:
        raise PermissionDenied("Нет доступа")
    return get_object_or_404(Employee, id=employee_id)
