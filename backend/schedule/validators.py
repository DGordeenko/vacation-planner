"""Проверка ссылок на задачи.

Стандартная проверка URL требует домен верхнего уровня и отвергает
внутрикорпоративные адреса вида https://jira/TASK-1, на которых
обычно и живут трекеры компании. Здесь требование мягче: схема
http или https и непустой хост.
"""

from urllib.parse import urlparse

from rest_framework import serializers


def validate_task_link(value):
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise serializers.ValidationError(
            "Ожидается ссылка вида https://tracker/TASK-1")
    return value


class TaskLinkField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_blank", True)
        kwargs.setdefault("max_length", 500)
        super().__init__(**kwargs)

    def run_validation(self, data=serializers.empty):
        value = super().run_validation(data)
        return validate_task_link(value)
