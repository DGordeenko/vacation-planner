"""Импорт производственного календаря из CSV.

Те же принципы, что и в импорте сотрудников: файл применяется целиком
или не применяется вовсе, форматы дат и кодировки распознаются
автоматически — кадровые и бухгалтерские выгрузки приходят разными.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction

from company.models import Holiday

DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"]

ALIASES = {
    "date": ["дата", "day", "число"],
    "name": ["название", "наименование", "праздник", "title", "описание"],
    "day_type": ["тип", "тип_дня", "type", "вид"],
}

TYPE_ALIASES = {
    Holiday.DayType.HOLIDAY: [
        "holiday", "праздник", "праздничный", "нерабочий",
        "выходной", "праздничный нерабочий", "н",
    ],
    Holiday.DayType.PRE_HOLIDAY_SHORT: [
        "pre_holiday_short", "сокращённый", "сокращенный",
        "предпраздничный", "короткий", "с",
    ],
    Holiday.DayType.TRANSFERRED_WORKDAY: [
        "transferred_workday", "перенос", "перенесённый",
        "перенесенный", "рабочий", "р",
    ],
}


@dataclass
class RowProblem:
    row: int
    column: str
    value: str
    message: str


@dataclass
class ImportReport:
    total_rows: int = 0
    created: int = 0
    updated: int = 0
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    preview: list = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return not self.problems


def _normalize_header(name: str) -> str:
    cleaned = (name or "").strip().lower().lstrip("\ufeff")
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    for canonical, variants in ALIASES.items():
        if cleaned == canonical or cleaned in variants:
            return canonical
    return cleaned


def _parse_date(raw: str):
    value = (raw or "").strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return False


def _parse_type(raw: str):
    value = (raw or "").strip().lower()
    if not value:
        return Holiday.DayType.HOLIDAY  # по умолчанию — праздничный нерабочий
    for day_type, variants in TYPE_ALIASES.items():
        if value == day_type or value in variants:
            return day_type
    return False


def _sniff(text: str) -> str:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


@transaction.atomic
def import_holidays(raw_bytes: bytes, year: int = None, replace: bool = False,
                    dry_run: bool = False) -> ImportReport:
    """replace=True очищает календарь года перед загрузкой."""
    report = ImportReport()

    text = None
    for encoding in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        report.problems.append(RowProblem(0, "", "", "Не удалось определить кодировку файла"))
        return report

    reader = csv.DictReader(io.StringIO(text), delimiter=_sniff(text))
    if not reader.fieldnames:
        report.problems.append(RowProblem(0, "", "", "Файл пуст или не содержит заголовков"))
        return report

    reader.fieldnames = [_normalize_header(name) for name in reader.fieldnames]
    for required in ("date", "name"):
        if required not in reader.fieldnames:
            report.problems.append(RowProblem(
                0, required, "", f"В файле нет обязательной колонки «{required}»"))
    if report.problems:
        return report

    rows = list(reader)
    report.total_rows = len(rows)

    seen = {}
    parsed = []
    for index, row in enumerate(rows, start=2):
        day = _parse_date(row.get("date"))
        if day is None:
            report.problems.append(RowProblem(index, "date", "", "Пустая дата"))
            continue
        if day is False:
            report.problems.append(RowProblem(
                index, "date", row.get("date", ""), "Дата в нераспознанном формате"))
            continue

        name = (row.get("name") or "").strip()
        if not name:
            report.problems.append(RowProblem(index, "name", "", "Пустое название"))

        day_type = _parse_type(row.get("day_type"))
        if day_type is False:
            report.problems.append(RowProblem(
                index, "day_type", row.get("day_type", ""),
                "Неизвестный тип дня. Допустимы: праздничный, сокращённый, перенесённый"))
            continue

        if day in seen:
            report.problems.append(RowProblem(
                index, "date", str(day),
                f"Дата повторяется (первый раз в строке {seen[day]})"))
        else:
            seen[day] = index

        if year and day.year != year:
            report.problems.append(RowProblem(
                index, "date", str(day),
                f"Дата относится к {day.year} году, а загружается календарь на {year}"))

        parsed.append({"row": index, "date": day, "name": name, "day_type": day_type})

    years = {item["date"].year for item in parsed}
    if len(years) > 1 and not year:
        report.warnings.append(RowProblem(
            0, "date", ", ".join(str(y) for y in sorted(years)),
            "В файле даты нескольких лет"))

    report.preview = [
        {"date": item["date"], "name": item["name"], "day_type": item["day_type"]}
        for item in parsed
    ]

    if report.problems or dry_run:
        transaction.set_rollback(True)
        return report

    if replace and years:
        for target_year in years:
            Holiday.objects.filter(date__year=target_year).delete()

    for item in parsed:
        _, created = Holiday.objects.update_or_create(
            date=item["date"],
            defaults={"name": item["name"], "day_type": item["day_type"]},
        )
        if created:
            report.created += 1
        else:
            report.updated += 1

    return report
