"""Настройки доменного входа читаются из переменных окружения.

Проверяется в отдельном процессе: настройки Django загружаются один раз
при старте, и подменить окружение внутри уже запущенных тестов нельзя.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BACKEND = Path(__file__).resolve().parent.parent
PROBE = ("import json, django; django.setup(); from django.conf import settings as s; "
         "print(json.dumps({'password': s.SSO_ALLOW_PASSWORD_LOGIN, 'krb': s.SSO_KERBEROS, "
         "'oidc': s.SSO_OIDC}))")


def read_settings(**env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("OIDC_", "KERBEROS_", "KRB5_", "SSO_"))}
    result = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=BACKEND, capture_output=True, text=True,
        env={**base, "DJANGO_SETTINGS_MODULE": "config.settings", "SECRET_KEY": "t", **env},
        check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


class SsoEnvironmentTests(SimpleTestCase):
    def test_defaults_keep_domain_login_off(self):
        values = read_settings()
        self.assertTrue(values["password"])
        self.assertFalse(values["krb"]["enabled"])
        self.assertFalse(values["oidc"]["enabled"])

    def test_keycloak_and_kerberos_from_environment(self):
        values = read_settings(
            OIDC_ENABLED="True", OIDC_SERVER_URL="https://sso.corp.ru", OIDC_REALM="corp",
            OIDC_CLIENT_ID="vacation", OIDC_CLIENT_SECRET="s3cret",
            OIDC_REDIRECT_URI="https://vacation.corp.ru/", OIDC_VERIFY_TLS="False",
            KERBEROS_ENABLED="1", KRB5_KTNAME="/etc/vacation.keytab",
            KERBEROS_REALM_MAP="CORP.LOCAL=corp.ru, BRANCH.LOCAL=branch.corp.ru",
            SSO_ALLOW_PASSWORD_LOGIN="False")
        self.assertFalse(values["password"])
        self.assertEqual(values["oidc"]["server_url"], "https://sso.corp.ru")
        self.assertEqual(values["oidc"]["client_secret"], "s3cret")
        self.assertFalse(values["oidc"]["verify_tls"])
        self.assertEqual(values["oidc"]["scope"], "openid email profile")
        self.assertTrue(values["krb"]["enabled"])
        self.assertEqual(values["krb"]["realm_to_email_domain"],
                         {"CORP.LOCAL": "corp.ru", "BRANCH.LOCAL": "branch.corp.ru"})
