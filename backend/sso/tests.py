"""Тесты доменной аутентификации.

Внешние системы подменяются: проверяется логика допуска, разбор
принципалов и обработка отказов, а не работа Kerberos и Keycloak.
"""

import base64
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from employees.models import Employee, OrgUnit
from sso.providers.base import DomainIdentity, ProviderError, ProviderNotConfigured
from sso.providers.kerberos import KerberosProvider
from sso.providers.oidc import OidcProvider
from sso.services import AccessDenied, login_with_identity, resolve_employee

KERBEROS_ON = {
    "enabled": True,
    "keytab": "/etc/krb5.keytab",
    "realm_to_email_domain": {"CORP.LOCAL": "corp.ru"},
    "email_domain": "corp.ru",
}

OIDC_ON = {
    "enabled": True,
    "server_url": "https://keycloak.corp.ru",
    "realm": "employees",
    "client_id": "vacation-app",
    "client_secret": "secret",
    "redirect_uri": "https://vacation.corp.ru/auth/callback",
    "scope": "openid email profile",
    "verify_tls": True,
    "timeout": 10,
}


class IdentityResolutionTests(TestCase):
    """Кого пускать: главное решение модуля."""

    def setUp(self):
        unit = OrgUnit.objects.create(
            name="Отдел С", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.employee = Employee.objects.create_user(
            email="ivanov@corp.ru", employee_number="S1",
            full_name="Иванов Иван", hire_date=date(2020, 1, 1), org_unit=unit)

    def test_known_employee_allowed(self):
        identity = DomainIdentity(email="ivanov@corp.ru")
        self.assertEqual(resolve_employee(identity), self.employee)

    def test_email_case_is_ignored(self):
        identity = DomainIdentity(email="IVANOV@CORP.RU")
        self.assertEqual(resolve_employee(identity), self.employee)

    def test_unknown_employee_denied(self):
        """Домен подтверждает личность, но не даёт права на существование."""
        identity = DomainIdentity(email="stranger@corp.ru")
        with self.assertRaises(AccessDenied) as ctx:
            resolve_employee(identity)
        self.assertEqual(ctx.exception.code, "employee_not_found")
        self.assertEqual(Employee.objects.count(), 1)

    def test_terminated_employee_denied(self):
        self.employee.status = Employee.Status.INACTIVE
        self.employee.save()
        with self.assertRaises(AccessDenied) as ctx:
            resolve_employee(DomainIdentity(email="ivanov@corp.ru"))
        self.assertEqual(ctx.exception.code, "employee_inactive")

    def test_domain_login_clears_invitation(self):
        self.employee.invitation_token = "11111111-1111-1111-1111-111111111111"
        self.employee.save()
        employee, token = login_with_identity(DomainIdentity(email="ivanov@corp.ru"))
        employee.refresh_from_db()
        self.assertIsNone(employee.invitation_token)
        self.assertTrue(token)


class KerberosProviderTests(TestCase):
    def setUp(self):
        self.provider = KerberosProvider(config=KERBEROS_ON)

    def test_header_parsing(self):
        token = base64.b64encode(b"ticket").decode()
        self.assertEqual(self.provider.parse_header(f"Negotiate {token}"), b"ticket")

    def test_other_schemes_ignored(self):
        self.assertIsNone(self.provider.parse_header("Basic abc"))
        self.assertIsNone(self.provider.parse_header(None))

    def test_broken_token_rejected(self):
        with self.assertRaises(ProviderError):
            self.provider.parse_header("Negotiate !!!не-base64!!!")

    def test_realm_mapped_to_email_domain(self):
        """В AD realm и почтовый домен часто различаются."""
        self.assertEqual(
            self.provider.principal_to_email("ivanov@CORP.LOCAL"), "ivanov@corp.ru")

    def test_unmapped_realm_used_as_is(self):
        self.assertEqual(
            self.provider.principal_to_email("ivanov@other.ru"), "ivanov@other.ru")

    def test_principal_without_realm(self):
        self.assertEqual(self.provider.principal_to_email("ivanov"), "ivanov@corp.ru")

    def test_principal_without_realm_and_without_setting(self):
        provider = KerberosProvider(config={"enabled": True, "keytab": "/k"})
        with self.assertRaises(ProviderError):
            provider.principal_to_email("ivanov")

    def test_missing_library_reports_clearly(self):
        """Без gssapi приложение не падает, а объясняет, чего не хватает."""
        with patch("sso.providers.kerberos._load_gssapi",
                   side_effect=ProviderNotConfigured("Модуль gssapi не установлен")):
            with self.assertRaises(ProviderNotConfigured) as ctx:
                self.provider.authenticate("Negotiate " +
                                           base64.b64encode(b"t").decode())
        self.assertIn("gssapi", str(ctx.exception))

    def test_not_configured_when_disabled(self):
        self.assertFalse(KerberosProvider(config={"enabled": False}).is_configured())
        self.assertTrue(self.provider.is_configured())


class OidcProviderTests(TestCase):
    def setUp(self):
        self.provider = OidcProvider(config=OIDC_ON)

    def test_authorize_url(self):
        url, state = self.provider.build_authorize_url()
        self.assertIn("https://keycloak.corp.ru/realms/employees", url)
        self.assertIn("client_id=vacation-app", url)
        self.assertIn("response_type=code", url)
        self.assertTrue(state)

    def test_not_configured_lists_missing(self):
        provider = OidcProvider(config={"enabled": True, "server_url": "x"})
        with self.assertRaises(ProviderNotConfigured) as ctx:
            provider.build_authorize_url()
        self.assertIn("realm", str(ctx.exception))

    def test_token_without_email_rejected(self):
        """Без почты сопоставить сотрудника невозможно."""
        with patch.object(self.provider, "exchange_code",
                          return_value={"id_token": "x"}), \
             patch.object(self.provider, "verify_id_token",
                          return_value={"sub": "123"}):
            with self.assertRaises(ProviderError) as ctx:
                self.provider.authenticate("code")
        self.assertEqual(ctx.exception.code, "email_claim_missing")

    def test_identity_from_claims(self):
        with patch.object(self.provider, "exchange_code",
                          return_value={"id_token": "x"}), \
             patch.object(self.provider, "verify_id_token", return_value={
                 "email": "Ivanov@Corp.RU", "name": "Иванов Иван",
                 "preferred_username": "ivanov"}):
            identity = self.provider.authenticate("code")
        self.assertEqual(identity.email, "ivanov@corp.ru")
        self.assertEqual(identity.display_name, "Иванов Иван")

    def test_missing_id_token(self):
        with patch.object(self.provider, "exchange_code", return_value={}):
            with self.assertRaises(ProviderError) as ctx:
                self.provider.authenticate("code")
        self.assertEqual(ctx.exception.code, "no_id_token")


class AuthMethodsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_methods_listed(self):
        body = self.client.get("/api/v1/auth/methods/").json()
        self.assertIn("password_login", body)
        self.assertEqual({p["name"] for p in body["providers"]},
                         {"kerberos", "keycloak"})

    @override_settings(SSO_KERBEROS=KERBEROS_ON, SSO_OIDC=OIDC_ON)
    def test_configured_providers_marked(self):
        body = self.client.get("/api/v1/auth/methods/").json()
        self.assertTrue(all(p["configured"] for p in body["providers"]))

    def test_disabled_providers_marked(self):
        body = self.client.get("/api/v1/auth/methods/").json()
        self.assertFalse(any(p["configured"] for p in body["providers"]))


class NegotiateApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        unit = OrgUnit.objects.create(
            name="Отдел К", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.employee = Employee.objects.create_user(
            email="ivanov@corp.ru", employee_number="K1",
            full_name="Иванов Иван", hire_date=date(2020, 1, 1), org_unit=unit)

    def test_disabled_returns_503(self):
        response = self.client.get("/api/v1/auth/negotiate/")
        self.assertEqual(response.status_code, 503)

    @override_settings(SSO_KERBEROS=KERBEROS_ON)
    def test_without_ticket_asks_for_negotiate(self):
        """Первый запрос без билета — штатная часть протокола."""
        response = self.client.get("/api/v1/auth/negotiate/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], "Negotiate")

    @override_settings(SSO_KERBEROS=KERBEROS_ON)
    def test_successful_login(self):
        identity = DomainIdentity(email="ivanov@corp.ru",
                                  principal="ivanov@CORP.LOCAL", provider="kerberos")
        with patch.object(KerberosProvider, "authenticate", return_value=identity):
            response = self.client.get("/api/v1/auth/negotiate/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["token"])
        self.assertEqual(body["employee_id"], str(self.employee.id))

    @override_settings(SSO_KERBEROS=KERBEROS_ON)
    def test_unknown_user_gets_explanation(self):
        identity = DomainIdentity(email="stranger@corp.ru", provider="kerberos")
        with patch.object(KerberosProvider, "authenticate", return_value=identity):
            response = self.client.get("/api/v1/auth/negotiate/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "employee_not_found")

    @override_settings(SSO_KERBEROS=KERBEROS_ON)
    def test_token_works_for_api(self):
        identity = DomainIdentity(email="ivanov@corp.ru", provider="kerberos")
        with patch.object(KerberosProvider, "authenticate", return_value=identity):
            token = self.client.get("/api/v1/auth/negotiate/").json()["token"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["full_name"], "Иванов Иван")


class OidcApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        unit = OrgUnit.objects.create(
            name="Отдел О", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.employee = Employee.objects.create_user(
            email="petrov@corp.ru", employee_number="O1",
            full_name="Петров Пётр", hire_date=date(2020, 1, 1), org_unit=unit)

    @override_settings(SSO_OIDC=OIDC_ON)
    def test_start_returns_url(self):
        body = self.client.get("/api/v1/auth/oidc/start/").json()
        self.assertIn("authorize_url", body)
        self.assertIn("state", body)

    def test_start_when_not_configured(self):
        self.assertEqual(
            self.client.get("/api/v1/auth/oidc/start/").status_code, 503)

    @override_settings(SSO_OIDC=OIDC_ON)
    def test_callback_success(self):
        identity = DomainIdentity(email="petrov@corp.ru", provider="keycloak")
        self.client.get("/api/v1/auth/oidc/start/")
        with patch.object(OidcProvider, "authenticate", return_value=identity):
            response = self.client.get("/api/v1/auth/oidc/callback/?code=abc")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["token"])

    @override_settings(SSO_OIDC=OIDC_ON)
    def test_state_mismatch_rejected(self):
        """Несовпадение state — признак подделки запроса."""
        session = self.client.session
        session["sso_oidc_state"] = "ожидаемый"
        session.save()
        response = self.client.get(
            "/api/v1/auth/oidc/callback/?code=abc&state=подменённый")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "state_mismatch")

    @override_settings(SSO_OIDC=OIDC_ON)
    def test_provider_declined(self):
        response = self.client.get(
            "/api/v1/auth/oidc/callback/?error=access_denied"
            "&error_description=Пользователь+отказал")
        self.assertEqual(response.status_code, 401)

    @override_settings(SSO_OIDC=OIDC_ON)
    def test_code_required(self):
        self.assertEqual(
            self.client.get("/api/v1/auth/oidc/callback/").status_code, 400)


class PasswordFallbackTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        Employee.objects.create_user(
            email="user@corp.ru", employee_number="P1", full_name="Пользователь",
            hire_date=date(2020, 1, 1), password="pw123456")

    def test_password_login_works_by_default(self):
        response = self.client.post("/api/v1/auth/login/", {
            "email": "user@corp.ru", "password": "pw123456"}, format="json")
        self.assertEqual(response.status_code, 200)

    @override_settings(SSO_ALLOW_PASSWORD_LOGIN=False)
    def test_password_login_can_be_disabled(self):
        response = self.client.post("/api/v1/auth/login/", {
            "email": "user@corp.ru", "password": "pw123456"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "password_login_disabled")
