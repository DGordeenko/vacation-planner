"""Пробы состояния сервиса.

- `/api/health/live/` — процесс жив и отвечает; базу не трогает. Для
  liveness: при недоступной базе перезапуск пода не поможет, а лишь
  умножит нагрузку на восстанавливающуюся базу.
- `/api/health/ready/` — сервис готов принимать запросы: база отвечает.
  Для readiness и балансировщиков.
- `/api/health/` — то же, что ready; оставлен ради совместимости
  (Railway, docker compose, старые манифесты).

Эндпоинты публичные: проверяющему незачем знать учётные данные. Поэтому
текст ошибки базы наружу не отдаётся — в нём бывают хост, имя базы
и пользователь. Он пишется в журнал на уровне WARNING.
"""

import logging

from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class LiveView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"status": "ok"})


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as failure:
            # Недоступная база — это «сервис нездоров», а не ошибка сервера:
            # проверяющий должен получить понятный ответ, а не стек 500.
            logger.warning("Проверка готовности: база недоступна: %s", failure)
            return Response({"status": "degraded", "database": "unavailable"},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"status": "ok", "database": "ok"})
