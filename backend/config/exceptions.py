"""Ответы на ошибки API.

Неверный параметр запроса (`?employee_id=abc`, `?date_from=вчера`) Django
отвергает исключением ValidationError уже при построении запроса к базе.
DRF его не знает и отдаёт 500 — клиент видит «ошибку сервера», а в журнале
копится шум. Здесь такие ошибки превращаются в 400 с понятным текстом.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None and isinstance(exc, DjangoValidationError):
        return Response({"detail": "Неверный параметр запроса",
                         "errors": exc.messages, "code": "bad_parameter"},
                        status=status.HTTP_400_BAD_REQUEST)
    return response
