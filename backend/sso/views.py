"""Эндпоинты доменной аутентификации."""

import secrets

from django.shortcuts import redirect
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from sso import services
from sso.providers.base import ProviderError
from sso.providers.kerberos import KerberosProvider
from sso.providers.oidc import OidcProvider

STATE_SESSION_KEY = "sso_oidc_state"


def _error(error: ProviderError):
    return Response({"detail": str(error), "code": error.code},
                    status=error.status)


class AuthMethodsView(APIView):
    """Что показать на экране входа: какие способы доступны."""

    permission_classes = [AllowAny]

    def get(self, request):
        from demo.views import demo_methods
        return Response({
            "password_login": services.password_login_allowed(),
            "providers": services.available_providers(),
            "demo": demo_methods(),
        })


class NegotiateView(APIView):
    """Вход по Kerberos.

    Браузер сначала получает 401 с заголовком WWW-Authenticate: Negotiate
    и повторяет запрос уже с билетом. Это штатная часть протокола,
    а не ошибка.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        provider = KerberosProvider()
        if not provider.is_configured():
            return Response(
                {"detail": "Вход по домену не настроен",
                 "code": "provider_not_configured"}, status=503)

        header = request.META.get("HTTP_AUTHORIZATION")
        try:
            identity = provider.authenticate(header)
            employee, token = services.login_with_identity(identity)
        except ProviderError as error:
            response = _error(error)
            if error.code in ("negotiate_required", "invalid_ticket"):
                response["WWW-Authenticate"] = "Negotiate"
            return response

        return Response({
            "token": token,
            "employee_id": str(employee.id),
            "full_name": employee.full_name,
            "provider": identity.provider,
        })


class OidcStartView(APIView):
    """Начало входа через Keycloak — выдаёт ссылку для редиректа."""

    permission_classes = [AllowAny]

    def get(self, request):
        provider = OidcProvider()
        try:
            url, state = provider.build_authorize_url()
        except ProviderError as error:
            return _error(error)

        request.session[STATE_SESSION_KEY] = state
        if request.query_params.get("redirect") == "1":
            return redirect(url)
        return Response({"authorize_url": url, "state": state})


class OidcCallbackView(APIView):
    """Возврат из Keycloak с кодом авторизации."""

    permission_classes = [AllowAny]

    def get(self, request):
        return self._handle(request, request.query_params)

    def post(self, request):
        return self._handle(request, request.data)

    def _handle(self, request, payload):
        if payload.get("error"):
            return Response(
                {"detail": payload.get("error_description") or payload["error"],
                 "code": "provider_declined"}, status=401)

        code = payload.get("code")
        if not code:
            return Response({"detail": "Не передан код авторизации",
                             "code": "code_missing"}, status=400)

        expected = request.session.pop(STATE_SESSION_KEY, None)
        received = payload.get("state")
        # Сверка state защищает от подделки запроса: без неё чужой
        # ответ Keycloak можно подсунуть авторизованному браузеру.
        # Сравнение байтов, а не строк: compare_digest падает на не-ASCII,
        # и подделанный state с кириллицей обрушил бы обработчик.
        if expected and received and not secrets.compare_digest(
            str(expected).encode("utf-8"), str(received).encode("utf-8")
        ):
            return Response({"detail": "Не совпал параметр state",
                             "code": "state_mismatch"}, status=400)

        provider = OidcProvider()
        try:
            identity = provider.authenticate(code)
            employee, token = services.login_with_identity(identity)
        except ProviderError as error:
            return _error(error)

        return Response({
            "token": token,
            "employee_id": str(employee.id),
            "full_name": employee.full_name,
            "provider": identity.provider,
        })
