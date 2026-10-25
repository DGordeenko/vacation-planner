"""Проверка демо-токена.

Тот же механизм, что у рабочих токенов DRF, но со схемой ``Demo``.
Токены лежат в таблице демо-базы: middleware переключил запрос на неё
до того, как дело дошло до проверки.
"""

from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication

from demo.db import demo_enabled, is_demo_active
from demo.middleware import SCHEME


class DemoTokenAuthentication(TokenAuthentication):
    keyword = SCHEME

    def authenticate_credentials(self, key):
        # Без переключения на демо-базу токен искался бы в рабочей.
        # Middleware переключает раньше, но проверка не должна от него зависеть.
        if not (demo_enabled() and is_demo_active()):
            raise exceptions.AuthenticationFailed("Демо-режим выключен")
        return super().authenticate_credentials(key)
