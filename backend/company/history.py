"""Настройки во времени: какое значение действовало в нужном месяце.

Версионируются годовая норма, ставка начисления, длина рабочего дня, пороги
правил проверки и своя норма сотрудника (решение ноября 2026). Значение
действует с первого числа месяца до следующего изменения.

Пока истории нет, действует текущее значение (поле настроек, порог правила,
поле сотрудника) — как до появления версий. При первом изменении «с даты»
прежнее значение сохраняется строкой «с начала времён», чтобы прошлые
месяцы считались по нему, а не по новому.

Как значения применяются в расчётах — `docs/04-algorithms.md`, раздел
«Настройки во времени».
"""

from bisect import bisect_right
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from company.models import CompanySettings, SettingVersion

Key = SettingVersion.Key
BEGINNING = date(2000, 1, 1)
COMPANY_KEYS = [Key.VACATION_NORM, Key.ACCRUAL_RATE, Key.WORKDAY_HOURS]


def month_start(day: date) -> date:
    return day.replace(day=1)


class Timeline:
    """Значения одной настройки по месяцам. Загружается одним запросом.

    `current` — значение, если истории нет. В расчётах по месяцам (начисление
    за годы стажа) это избавляет от запроса на каждый месяц.
    """

    def __init__(self, key, current, rule_code="", employee=None):
        rows = SettingVersion.objects.filter(key=key, rule_code=rule_code, employee=employee)
        pairs = sorted((row.effective_from, row.value) for row in rows)
        self.dates = [day for day, _value in pairs]
        self.values = [value for _day, value in pairs]
        self.current = current

    def on(self, day: date):
        index = bisect_right(self.dates, day)
        return self.values[index - 1] if index else self.current

    @property
    def is_versioned(self):
        return bool(self.dates)

    def rows(self):
        return [{"effective_from": day, "value": None if value is None else float(value)}
                for day, value in zip(self.dates, self.values, strict=True)]


def company_timeline(key, settings_row=None):
    settings_row = settings_row or CompanySettings.get_solo()
    return Timeline(key, getattr(settings_row, key))


def company_value(key, day: date = None):
    return company_timeline(key).on(day or date.today())


def rule_timeline(rule):
    return Timeline(Key.RULE_THRESHOLD, rule.threshold_value, rule_code=rule.code)


def rule_threshold(rule, day: date = None):
    """Порог правила на дату."""
    return rule_timeline(rule).on(day or date.today()) if rule else None


def employee_timeline(employee):
    return Timeline(Key.EMPLOYEE_NORM, employee.annual_vacation_norm_override,
                    employee=employee)


class EmployeeRates:
    """Норма и ставка сотрудника по месяцам — для начисления и годовой нормы.

    Своя норма меняет ставку в той же пропорции: ставка компании × своя
    норма / норма компании, на каждый месяц по действующим в нём значениям.
    """

    def __init__(self, employee):
        settings_row = CompanySettings.get_solo()
        self.norm = company_timeline(Key.VACATION_NORM, settings_row)
        self.rate = company_timeline(Key.ACCRUAL_RATE, settings_row)
        self.own = employee_timeline(employee)

    def norm_on(self, day: date) -> Decimal:
        return self.own.on(day) or self.norm.on(day)

    def rate_on(self, day: date) -> Decimal:
        own, company_norm = self.own.on(day), self.norm.on(day)
        rate = self.rate.on(day)
        if not own or own == company_norm:
            return rate
        return (rate * own / company_norm).quantize(Decimal("0.01"))

    def annual_norm(self, year: int) -> int:
        """Норма за год: по месяцам, двенадцатая часть действующей в каждом.

        28 дней до июля и 31 после — 29,5, округляется до 30. Пока норма
        не менялась, это ровно она сама.
        """
        months = [self.norm_on(date(year, m, 1)) for m in range(1, 13)]
        if len(set(months)) == 1:
            return int(months[0])
        return int((sum(months) / 12).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@transaction.atomic
def set_value(key, effective_from: date, value, current, rule_code="", employee=None):
    """Записать значение с первого числа месяца.

    current — значение, действовавшее до появления истории: при первой
    записи оно сохраняется «с начала времён», чтобы прошлое не изменилось.
    Возвращает значение на сегодня — его вызывающий хранит в поле настройки.
    """
    effective_from = month_start(effective_from)
    scope = {"key": key, "rule_code": rule_code, "employee": employee}
    if effective_from > BEGINNING and not SettingVersion.objects.filter(**scope).exists():
        SettingVersion.objects.create(effective_from=BEGINNING, value=current, **scope)
    SettingVersion.objects.update_or_create(
        effective_from=effective_from, defaults={"value": value}, **scope)
    today = Timeline(current=current, **scope).on(date.today())
    _compact(scope)
    return today


def _compact(scope):
    """Убирает строки, не меняющие значения.

    Если осталась одна строка «как в общей настройке» (своя норма сотрудника:
    поставили 35 и в том же месяце вернули общую), история удаляется целиком —
    сотрудник не числится «со своей нормой». Единственная строка со значением
    остаётся: после неё поле настройки могло уже хранить другое значение.
    """
    rows = list(SettingVersion.objects.filter(**scope).order_by("effective_from"))
    previous = object()
    for row in rows:
        if row.value == previous:
            row.delete()
        else:
            previous = row.value
    remaining = SettingVersion.objects.filter(**scope)
    if remaining.count() == 1 and remaining.get().value is None:
        remaining.delete()


def remove_value(key, effective_from: date, current, rule_code="", employee=None):
    """Убрать изменение «с даты»: до следующего действует предыдущее значение.

    Начальное значение («с начала времён») не удаляется — без него прошлые
    месяцы потеряли бы опору.
    """
    if month_start(effective_from) == BEGINNING:
        return Timeline(key, current, rule_code, employee).on(date.today())
    scope = {"key": key, "rule_code": rule_code, "employee": employee}
    SettingVersion.objects.filter(effective_from=month_start(effective_from), **scope).delete()
    today = Timeline(current=current, **scope).on(date.today())
    _compact(scope)
    return today
