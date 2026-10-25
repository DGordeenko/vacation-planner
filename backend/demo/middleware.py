"""Переключение запроса на демо-базу по заголовку авторизации.

Демо-клиент передаёт токен со схемой ``Demo`` вместо ``Token``. Такой
запрос целиком, включая проверку токена, выполняется на демо-базе.
Токен рабочей системы в демо-базе не найдётся, демо-токен — в рабочей,
поэтому перепутать данные нельзя даже подменой заголовка.

Стоит до SessionMiddleware и AuthenticationMiddleware: всё, что они
читают из базы, тоже должно прийти из демо-базы.
"""

from demo.db import demo_enabled, use_demo_database

SCHEME = "Demo"


def is_demo_request(request) -> bool:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    return header.split(" ", 1)[0] == SCHEME


class DemoDatabaseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if demo_enabled() and is_demo_request(request):
            with use_demo_database():
                return self.get_response(request)
        return self.get_response(request)
