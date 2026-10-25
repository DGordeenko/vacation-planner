"""Вход в демо-режим выбором роли, без пароля.

Учётные записи демо вымышленные и живут в отдельной базе, поэтому пароль
ничего не защищает, а только мешает посетителю. Все, кто выбрал одну
роль, входят под одним и тем же сотрудником и видят изменения друг друга.
"""

from django.db import DatabaseError, transaction
from django.http import Http404
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from demo import data
from demo.db import demo_enabled, use_demo_database
from demo.middleware import SCHEME
from employees.models import Employee


def _role_employees():
    """Сотрудники демо-ролей. Пусто, если демо-данные ещё не загружены."""
    numbers = [role.employee_number for role in data.ROLES]
    try:
        with use_demo_database(), transaction.atomic():
            return {e.employee_number: e for e in Employee.objects.filter(
                employee_number__in=numbers, status=Employee.Status.ACTIVE)}
    except DatabaseError:
        # Демо-база не создана: миграции для неё ещё не применялись.
        return {}


def demo_methods() -> dict:
    """Блок для /auth/methods/: показывать ли кнопку и какие роли."""
    if not demo_enabled():
        return {"enabled": False, "roles": []}
    employees = _role_employees()
    roles = [
        {"code": role.code, "title": role.title, "description": role.description,
         "full_name": employees[role.employee_number].full_name}
        for role in data.ROLES if role.employee_number in employees
    ]
    # Кнопка не показывается, пока данные не загружены: вход всё равно
    # закончился бы ошибкой.
    return {"enabled": bool(roles), "roles": roles}


class DemoLoginView(APIView):
    permission_classes = [AllowAny]
    # Без аутентификации: сессия рабочей системы в этом же браузере
    # не должна требовать CSRF-токен для входа в демо.
    authentication_classes = []

    def post(self, request):
        if not demo_enabled():
            raise Http404
        role = data.ROLES_BY_CODE.get(request.data.get("role"))
        if role is None:
            return Response(
                {"detail": "Неизвестная роль", "code": "unknown_role",
                 "roles": list(data.ROLES_BY_CODE)},
                status=status.HTTP_400_BAD_REQUEST)

        employee = _role_employees().get(role.employee_number)
        if employee is None:
            return Response(
                {"detail": "Демо-данные ещё не загружены, попробуйте через минуту",
                 "code": "demo_not_loaded"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE)

        with use_demo_database():
            token, _ = Token.objects.get_or_create(user=employee)
        return Response({
            "token": token.key, "employee_id": str(employee.id),
            "scheme": SCHEME, "role": role.code,
        })
