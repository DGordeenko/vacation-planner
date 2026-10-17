"""Аутентификация через Keycloak по OpenID Connect.

Поток authorization code: приложение отправляет браузер в Keycloak,
получает обратно код, обменивает его на токены и проверяет подпись
id_token по ключам realm (JWKS).

Проверка подписи обязательна: без неё id_token — просто строка,
которую можно подделать.
"""

import secrets
import time
from urllib.parse import urlencode

from django.conf import settings

from sso.providers.base import (
    BaseProvider, DomainIdentity, ProviderError, ProviderNotConfigured,
)

_REQUIRED = ("server_url", "realm", "client_id", "redirect_uri")


class OidcProvider(BaseProvider):
    name = "keycloak"

    def __init__(self, config=None):
        self.config = config or getattr(settings, "SSO_OIDC", {})
        self._jwks_cache = {"keys": None, "fetched_at": 0}

    # ---------------------------------------------------------------- setup

    def is_configured(self) -> bool:
        if not self.config.get("enabled"):
            return False
        return all(self.config.get(key) for key in _REQUIRED)

    def describe(self):
        return {
            "name": self.name,
            "title": self.config.get("button_title", "Войти через Keycloak"),
            "configured": self.is_configured(),
            "auto": False,
        }

    def _base(self):
        return (f"{self.config['server_url'].rstrip('/')}"
                f"/realms/{self.config['realm']}/protocol/openid-connect")

    def _require_config(self):
        if not self.is_configured():
            missing = [key for key in _REQUIRED if not self.config.get(key)]
            raise ProviderNotConfigured(
                "Keycloak не настроен: не заданы " + ", ".join(missing))

    # ------------------------------------------------------------- handshake

    def build_authorize_url(self, state: str = None) -> tuple:
        """Ссылка, на которую отправляется браузер, и state для сверки."""
        self._require_config()
        state = state or secrets.token_urlsafe(24)
        params = {
            "client_id": self.config["client_id"],
            "redirect_uri": self.config["redirect_uri"],
            "response_type": "code",
            "scope": self.config.get("scope", "openid email profile"),
            "state": state,
        }
        return f"{self._base()}/auth?{urlencode(params)}", state

    def exchange_code(self, code: str) -> dict:
        self._require_config()
        import requests

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.config["client_id"],
            "redirect_uri": self.config["redirect_uri"],
        }
        if self.config.get("client_secret"):
            data["client_secret"] = self.config["client_secret"]

        try:
            response = requests.post(
                f"{self._base()}/token", data=data,
                timeout=self.config.get("timeout", 10),
                verify=self.config.get("verify_tls", True))
        except Exception as error:
            raise ProviderError(f"Keycloak недоступен: {error}",
                                code="provider_unreachable", status=503) from error

        if response.status_code != 200:
            raise ProviderError(
                f"Keycloak отклонил код авторизации ({response.status_code})",
                code="code_rejected", status=401)
        return response.json()

    def _jwks(self):
        import requests

        ttl = self.config.get("jwks_cache_seconds", 3600)
        if self._jwks_cache["keys"] and time.time() - self._jwks_cache["fetched_at"] < ttl:
            return self._jwks_cache["keys"]

        try:
            response = requests.get(
                f"{self._base()}/certs",
                timeout=self.config.get("timeout", 10),
                verify=self.config.get("verify_tls", True))
            response.raise_for_status()
        except Exception as error:
            raise ProviderError(f"Не удалось получить ключи Keycloak: {error}",
                                code="jwks_unavailable", status=503) from error

        keys = response.json()
        self._jwks_cache = {"keys": keys, "fetched_at": time.time()}
        return keys

    def verify_id_token(self, id_token: str) -> dict:
        """Проверка подписи и срока действия id_token."""
        try:
            import jwt
            from jwt import PyJWKClient  # noqa: F401  (наличие зависимости)
        except ImportError as error:
            raise ProviderNotConfigured(
                "Нужен пакет pyjwt[crypto]: pip install 'pyjwt[crypto]'"
            ) from error

        from jwt.algorithms import RSAAlgorithm

        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        key_data = next(
            (key for key in self._jwks().get("keys", []) if key.get("kid") == kid),
            None)
        if key_data is None:
            raise ProviderError("Ключ подписи токена не найден в realm",
                                code="unknown_signing_key", status=401)

        try:
            public_key = RSAAlgorithm.from_jwk(key_data)
            return jwt.decode(
                id_token, public_key,
                algorithms=[header.get("alg", "RS256")],
                audience=self.config["client_id"],
                issuer=(f"{self.config['server_url'].rstrip('/')}"
                        f"/realms/{self.config['realm']}"),
            )
        except Exception as error:
            raise ProviderError(f"Токен не прошёл проверку: {error}",
                                code="invalid_token", status=401) from error

    def authenticate(self, code: str) -> DomainIdentity:
        tokens = self.exchange_code(code)
        id_token = tokens.get("id_token")
        if not id_token:
            raise ProviderError("Keycloak не вернул id_token",
                                code="no_id_token", status=401)

        claims = self.verify_id_token(id_token)
        email = (claims.get("email")
                 or claims.get("upn")
                 or claims.get("preferred_username") or "")
        if "@" not in email:
            raise ProviderError(
                "В токене нет почтового адреса — настройте mapper в Keycloak",
                code="email_claim_missing", status=401)

        return DomainIdentity(
            email=email.lower(),
            principal=claims.get("preferred_username", ""),
            display_name=claims.get("name", ""),
            provider=self.name,
            raw_claims=claims,
        )
