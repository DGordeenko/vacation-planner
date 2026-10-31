"""Аутентификация по Kerberos (SPNEGO).

Браузер, находящийся в домене, присылает билет в заголовке
Authorization: Negotiate <token>. Сервер проверяет его по keytab
и получает принципал вида user@REALM.

Требования к развёртыванию:
  * системные пакеты libkrb5-dev и krb5-user;
  * python-пакет gssapi;
  * keytab со служебным принципалом HTTP/<имя-сервера>@<REALM>;
  * переменная KRB5_KTNAME с путём к keytab.

Импорт gssapi отложенный: без него приложение должно запускаться
и работать по паролю, а не падать при старте.
"""

import base64
import os

from django.conf import settings

from sso.providers.base import (
    BaseProvider,
    DomainIdentity,
    ProviderError,
    ProviderNotConfigured,
)


def _load_gssapi():
    try:
        import gssapi  # noqa: WPS433
    except ImportError as error:
        raise ProviderNotConfigured(
            "Модуль gssapi не установлен. На сервере нужны пакеты "
            "libkrb5-dev и krb5-user, затем pip install gssapi"
        ) from error
    return gssapi


class KerberosProvider(BaseProvider):
    name = "kerberos"

    def __init__(self, config=None):
        self.config = config or getattr(settings, "SSO_KERBEROS", {})

    def is_configured(self) -> bool:
        if not self.config.get("enabled"):
            return False
        keytab = self.config.get("keytab") or os.environ.get("KRB5_KTNAME")
        return bool(keytab)

    def describe(self):
        return {
            "name": self.name,
            "title": "Вход по доменной учётной записи",
            "configured": self.is_configured(),
            "auto": True,  # браузер проходит проверку сам, без формы
        }

    def parse_header(self, header_value):
        """Разбор заголовка Authorization: Negotiate <token>."""
        if not header_value:
            return None
        parts = header_value.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "negotiate":
            return None
        try:
            return base64.b64decode(parts[1])
        except (ValueError, TypeError) as error:
            raise ProviderError("Повреждённый билет Kerberos",
                                code="bad_ticket", status=400) from error

    def principal_to_email(self, principal: str) -> str:
        """Принципал user@REALM → почтовый адрес.

        Сопоставление идёт по email (userPrincipalName), поэтому realm
        при необходимости заменяется на почтовый домен: в AD они часто
        различаются (CORP.LOCAL против corp.ru).
        """
        if "@" not in principal:
            domain = self.config.get("email_domain")
            if not domain:
                raise ProviderError(
                    f"Принципал «{principal}» без домена, а email_domain не задан",
                    code="principal_without_domain")
            return f"{principal}@{domain}".lower()

        user, realm = principal.rsplit("@", 1)
        mapping = self.config.get("realm_to_email_domain") or {}
        domain = mapping.get(realm.upper(), realm)
        return f"{user}@{domain}".lower()

    def authenticate(self, header_value) -> DomainIdentity:
        # Сначала разбор заголовка: приглашение браузеру прислать билет
        # не требует библиотеки, и отсутствие gssapi не должно подменять
        # штатный 401 ошибкой конфигурации.
        token = self.parse_header(header_value)
        if token is None:
            raise ProviderError("Билет Kerberos не передан",
                                code="negotiate_required", status=401)

        gssapi = _load_gssapi()

        keytab = self.config.get("keytab") or os.environ.get("KRB5_KTNAME")
        if keytab:
            os.environ["KRB5_KTNAME"] = keytab

        try:
            credentials = gssapi.Credentials(usage="accept")
            context = gssapi.SecurityContext(creds=credentials, usage="accept")
            context.step(token)
            if not context.complete:
                raise ProviderError(
                    "Не удалось завершить проверку билета",
                    code="handshake_incomplete", status=401)
            principal = str(context.initiator_name)
        except ProviderError:
            raise
        except Exception as error:  # библиотека бросает свои типы исключений
            raise ProviderError(f"Билет Kerberos отклонён: {error}",
                                code="invalid_ticket", status=401) from error

        return DomainIdentity(
            email=self.principal_to_email(principal),
            principal=principal,
            provider=self.name,
        )
