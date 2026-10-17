"""Текущий пользователь для сигналов аудита.

Сигналы моделей не знают о запросе, поэтому актор кладётся в contextvar
на время обработки запроса. Contextvar, а не thread-local: корректно
работает и в асинхронном режиме, и не течёт между запросами.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_actor: ContextVar = ContextVar("audit_actor", default=None)
_source: ContextVar = ContextVar("audit_source", default="system")


def get_actor():
    return _actor.get()


def get_source():
    return _source.get()


def set_actor(employee, source="api"):
    _actor.set(employee)
    _source.set(source)


def reset_actor():
    _actor.set(None)
    _source.set("system")


@contextmanager
def acting_as(employee, source="system"):
    """Для фоновых задач и импорта, где нет HTTP-запроса."""
    token_actor = _actor.set(employee)
    token_source = _source.set(source)
    try:
        yield
    finally:
        _actor.reset(token_actor)
        _source.reset(token_source)


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        set_actor(user if user and user.is_authenticated else None, source="api")
        try:
            return self.get_response(request)
        finally:
            reset_actor()
