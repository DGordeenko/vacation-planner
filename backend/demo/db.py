"""Изоляция демонстрационных данных.

Демо работает на том же экземпляре, что и рабочая система, но в отдельной
базе данных (псевдоним ``demo``). Справочники, праздники, настройки
и брендирование в модели данных глобальные, поэтому разделить демо
и рабочие данные внутри одной базы нельзя без правки каждой таблицы.

На время демо-запроса соединение ``default`` подменяется соединением
``demo``. Подмена действует на весь код без исключений: запросы ORM,
``transaction.atomic()`` без ``using``, сигналы аудита, сырые курсоры.
Альтернатива — маршрутизатор баз — не покрывает транзакции: ``atomic()``
без ``using`` открывает транзакцию в ``default``, а запись уходила бы
в ``demo`` вне её, и откат пробного импорта переставал бы работать.

Соединения Django хранятся по потокам (``thread_critical``), поэтому
подмена в одном запросе не видна параллельным запросам в других потоках.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections

DEMO_ALIAS = "demo"

_active: ContextVar = ContextVar("demo_database_active", default=False)


def demo_enabled() -> bool:
    return bool(getattr(settings, "DEMO_MODE_ENABLED", False))


def is_demo_active() -> bool:
    """Обрабатывается ли сейчас демо-запрос."""
    return _active.get()


def same_database_as_default() -> bool:
    """Демо-база по ошибке указывает на рабочую.

    Тогда загрузка демо-данных стёрла бы рабочие, поэтому такая настройка
    считается ошибкой конфигурации.
    """
    demo = settings.DATABASES.get(DEMO_ALIAS)
    default = settings.DATABASES[DEFAULT_DB_ALIAS]
    if demo is None:
        return False
    keys = ("ENGINE", "NAME", "HOST", "PORT")
    return all(str(demo.get(k, "")) == str(default.get(k, "")) for k in keys)


@contextmanager
def use_demo_database():
    """Выполнить блок кода на демо-базе.

    Повторный вход ничего не меняет: вложенный вызов уже работает
    с демо-базой.
    """
    if _active.get():
        yield
        return

    original = connections[DEFAULT_DB_ALIAS]
    connections[DEFAULT_DB_ALIAS] = connections[DEMO_ALIAS]
    token = _active.set(True)
    try:
        yield
    finally:
        connections[DEFAULT_DB_ALIAS] = original
        _active.reset(token)
