"""Разбор параметров запроса: неверное значение — 400 с пояснением, а не 500."""

from datetime import date

from rest_framework.exceptions import ValidationError


def parse_year(raw, field="year", default=None):
    if raw in (None, ""):
        return default if default is not None else date.today().year
    try:
        year = int(raw)
    except (TypeError, ValueError) as error:
        raise ValidationError({field: "Ожидается год, например 2027"}) from error
    if not 2000 <= year <= 2100:
        raise ValidationError({field: "Год вне допустимого диапазона"})
    return year


def parse_date(raw, field="date"):
    try:
        return date.fromisoformat(str(raw))
    except ValueError as error:
        raise ValidationError({field: "Ожидается дата ГГГГ-ММ-ДД"}) from error
