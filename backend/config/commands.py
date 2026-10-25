"""Общее для management-команд проекта."""

from django.db import DEFAULT_DB_ALIAS


class WorkingDatabaseChecks:
    """Системные проверки команды — только по рабочей базе.

    Django 6.1 перед любой командой проверяет модели на всех базах из
    DATABASES и для этого подключается к каждой, в том числе к демо-базе.
    Команды, которые работают только с рабочей базой и вызываются при
    старте контейнера или по расписанию, не должны падать из-за того,
    что демо-база недоступна: демо — витрина, а не часть рабочей системы.
    """

    def get_check_kwargs(self, options):
        return {**super().get_check_kwargs(options), "databases": [DEFAULT_DB_ALIAS]}
