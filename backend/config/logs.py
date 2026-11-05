"""Журналирование: JSON в stdout, без персональных данных.

Площадки (Kubernetes, облачные сервисы логов, Railway) собирают stdout
контейнера; JSON-строку они разбирают на поля без настройки парсеров.
Локально удобнее читать текст: LOG_FORMAT=text (docker compose).

Персональные данные в журнал не пишутся: журналы хранятся дольше
и доступны большему кругу людей, чем база. Код пишет в журнал
идентификаторы, а не ФИО и адреса; адреса почты, попавшие в сообщение
случайно (например, в тексте исключения), маскируются фильтром.
"""

import json
import logging
import re

EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Длинные шестнадцатеричные и UUID-подобные строки после token=/invite=/code=
SECRET_PARAM_RE = re.compile(r"((?:token|invite|code|state|password)=)[^&\s\"']+", re.I)


def mask(text):
    """«ivanov@corp.ru» → «i***@corp.ru»; значения token=… → ***."""
    text = EMAIL_RE.sub(r"\1***@\2", text)
    return SECRET_PARAM_RE.sub(r"\1***", text)


class MaskPersonalDataFilter(logging.Filter):
    """Маскирует почту и секретные параметры в итоговом тексте сообщения.

    Подставляет аргументы сразу (getMessage), чтобы маскировка видела
    готовую строку, а не шаблон с %s.
    """

    def filter(self, record):
        message = mask(record.getMessage())
        record.msg, record.args = message, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = mask(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    """Одна строка JSON на запись: время, уровень, логгер, сообщение."""

    def format(self, record):
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_text or record.exc_info:
            entry["exception"] = record.exc_text or self.formatException(record.exc_info)
        for key in ("event", "employee_id", "status_code"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, ensure_ascii=False)


def build(log_format, level):
    """Словарь для settings.LOGGING."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"mask_pd": {"()": "config.logs.MaskPersonalDataFilter"}},
        "formatters": {
            "json": {"()": "config.logs.JsonFormatter"},
            "text": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json" if log_format == "json" else "text",
                "filters": ["mask_pd"],
            },
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            # Запросы с ошибками Django пишет сам (django.request); стек
            # 500-х — только в журнал, клиенту он не уходит (DEBUG=False).
            "django": {"handlers": ["stdout"], "level": level, "propagate": False},
            # События безопасности: неудачный вход, превышение предела
            # попыток. Отдельный логгер — чтобы по нему строить оповещения.
            "security": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        },
    }
