"""Что происходит после успешной аутентификации.

Провайдер отвечает только на вопрос «кто это». Решение о допуске
принимается здесь и одинаково для Kerberos и Keycloak.
"""

from django.conf import settings
from rest_framework.authtoken.models import Token

from employees.models import Employee
from sso.providers.base import ProviderError
from sso.providers.kerberos import KerberosProvider
from sso.providers.oidc import OidcProvider


class AccessDenied(ProviderError):
    def __init__(self, message, code="access_denied"):
        super().__init__(message, code=code, status=403)


def get_provider(name):
    return {"kerberos": KerberosProvider, "keycloak": OidcProvider}.get(name, lambda: None)()


def available_providers():
    providers = [KerberosProvider(), OidcProvider()]
    return [provider.describe() for provider in providers]


def password_login_allowed() -> bool:
    return getattr(settings, "SSO_ALLOW_PASSWORD_LOGIN", True)


def resolve_employee(identity):
    """Сопоставление доменной учётки с сотрудником.

    Сопоставление идёт по email. Сотрудники заводятся только загрузкой
    из кадровой выгрузки: доменная аутентификация подтверждает личность,
    но не даёт права на существование в системе. Автосоздание завело бы
    людей без оргструктуры, а значит без руководителя и согласующего.
    """
    employee = Employee.objects.filter(email__iexact=identity.email).first()

    if employee is None:
        raise AccessDenied(
            f"Учётная запись {identity.email} прошла проверку в домене, "
            f"но сотрудник с таким адресом не загружен в систему. "
            f"Обратитесь к администратору",
            code="employee_not_found")

    if employee.status != Employee.Status.ACTIVE:
        raise AccessDenied(
            "Сотрудник отмечен как уволенный", code="employee_inactive")

    return employee


def issue_token(employee):
    token, _ = Token.objects.get_or_create(user=employee)
    return token.key


def login_with_identity(identity):
    employee = resolve_employee(identity)
    # Первый доменный вход снимает необходимость в приглашении.
    if employee.invitation_token:
        employee.invitation_token = None
        employee.save(update_fields=["invitation_token"])
    return employee, issue_token(employee)
