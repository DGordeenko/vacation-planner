"""Общий интерфейс провайдеров доменной аутентификации.

Kerberos и Keycloak решают одну задачу — назвать пользователя — и различаются
только способом. Общий интерфейс позволяет переключать их настройкой,
а вся логика доступа остаётся в одном месте (sso/services.py).
"""

from dataclasses import dataclass, field


@dataclass
class DomainIdentity:
    """Результат успешной аутентификации: кто это.

    Прав и ролей здесь нет намеренно — они берутся из справочника
    сотрудников, а не из домена: оргструктура ведётся в системе.
    """

    email: str
    principal: str = ""
    display_name: str = ""
    provider: str = ""
    raw_claims: dict = field(default_factory=dict)


class ProviderError(Exception):
    """Ошибка провайдера — превращается в понятный ответ API."""

    def __init__(self, message, code="provider_error", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


class ProviderNotConfigured(ProviderError):
    def __init__(self, message):
        super().__init__(message, code="provider_not_configured", status=503)


class BaseProvider:
    name = "base"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def describe(self) -> dict:
        """Что показать на экране входа."""
        return {"name": self.name, "configured": self.is_configured()}
