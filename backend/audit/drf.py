"""Определение автора изменения на уровне DRF.

Middleware здесь не подходит: при токенной аутентификации DRF определяет
пользователя внутри представления, уже после middleware, и все изменения
записывались бы на «Систему».

Проверка прав — первый хук, который выполняется после аутентификации
и при этом работает и с force_authenticate в тестах. Класс ничего не
запрещает: он только фиксирует, кто выполняет запрос.
"""

from rest_framework.permissions import BasePermission

from audit.context import set_actor


class RecordAuditActor(BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        set_actor(
            user if user and user.is_authenticated else None,
            source="api",
        )
        return True
