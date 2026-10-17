"""Проверка живости сервиса.

Используется healthcheck в docker compose, пробами Kubernetes и Railway.
Эндпоинт публичный: проверяющему незачем знать учётные данные.
"""

from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        database_ok, error = True, ""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as failure:
            # Недоступная база — это «сервис нездоров», а не ошибка сервера:
            # проверяющий должен получить понятный ответ, а не стек 500.
            database_ok = False
            error = str(failure)[:200]

        payload = {"status": "ok" if database_ok else "degraded",
                   "database": "ok" if database_ok else "unavailable"}
        if error:
            payload["detail"] = error

        return Response(
            payload,
            status=status.HTTP_200_OK if database_ok
            else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
