"""Импорт сотрудников из кадровой выгрузки.

Принцип из ФЗ: файл применяется целиком или не применяется вовсе.
Частичная загрузка оставила бы оргструктуру в неопределённом состоянии —
часть людей без руководителей, часть без подразделений.
"""

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from audit.context import acting_as
from employees.models import Employee, EmployeeTag, OrgUnit, Tag

# Кадровые системы выгружают даты по-разному — принимаем распространённые формы.
DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"]

UNIT_COLUMNS = [
    ("division", OrgUnit.UnitType.DIVISION),
    ("division_unit", OrgUnit.UnitType.DIVISION_UNIT),
    ("department", OrgUnit.UnitType.DEPARTMENT),
    ("group", OrgUnit.UnitType.GROUP),
]

REQUIRED = ["employee_number", "full_name", "email", "hire_date"]

# Синонимы заголовков: выгрузки редко совпадают с ожидаемыми именами.
ALIASES = {
    "employee_number": ["таб_номер", "табельный_номер", "tabnumber", "personnel_number", "id"],
    "full_name": ["фио", "ф_и_о", "name", "fullname"],
    "email": ["почта", "e_mail", "mail"],
    "hire_date": ["дата_приема", "дата_приёма", "hiredate", "start_date"],
    "manager_employee_number": ["руководитель", "manager", "manager_number", "chief"],
    "initial_balance_days": ["остаток", "остаток_отпуска", "balance", "vacation_balance"],
    "extra_tags": ["теги", "tags"],
    "termination_date": ["дата_увольнения", "fire_date"],
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
    return False  # отличаем «пусто» от «не разобрали»


def _parse_decimal(raw: str):
    value = (raw or "").strip().replace(",", ".").replace(" ", "")
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return False


def _sniff(text: str):
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


@transaction.atomic
def import_employees(raw_bytes: bytes, dry_run: bool = False) -> ImportReport:
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

    delimiter = _sniff(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        report.problems.append(RowProblem(0, "", "", "Файл пуст или не содержит заголовков"))
        return report

    reader.fieldnames = [_normalize_header(name) for name in reader.fieldnames]
    missing = [column for column in REQUIRED if column not in reader.fieldnames]
    if missing:
        report.problems.append(RowProblem(
            0, ", ".join(missing), "",
            f"В файле нет обязательных колонок: {', '.join(missing)}"))
        return report

    rows = list(reader)
    report.total_rows = len(rows)

    seen_numbers, seen_emails = {}, {}
    parsed = []

    for index, row in enumerate(rows, start=2):  # строка 1 — заголовок
        number = (row.get("employee_number") or "").strip()
        full_name = (row.get("full_name") or "").strip()
        email = (row.get("email") or "").strip().lower()

        if not number:
            report.problems.append(
                RowProblem(index, "employee_number", "", "Пустой табельный номер"))
            continue
        if not full_name:
            report.problems.append(RowProblem(index, "full_name", "", "Пустое ФИО"))
        if not email:
            report.problems.append(RowProblem(index, "email", "", "Пустой email"))
        elif "@" not in email:
            report.problems.append(RowProblem(index, "email", email, "Email без символа @"))

        if number in seen_numbers:
            report.problems.append(RowProblem(
                index, "employee_number", number,
                f"Табельный номер повторяется (первый раз в строке {seen_numbers[number]})"))
        else:
            seen_numbers[number] = index

        if email:
            if email in seen_emails:
                report.problems.append(RowProblem(
                    index, "email", email,
                    f"Email повторяется (первый раз в строке {seen_emails[email]})"))
            else:
                seen_emails[email] = index

        hire = _parse_date(row.get("hire_date"))
        if hire is None:
            report.problems.append(RowProblem(index, "hire_date", "", "Пустая дата приёма"))
        elif hire is False:
            report.problems.append(RowProblem(
                index, "hire_date", row.get("hire_date", ""),
                "Дата приёма в нераспознанном формате"))
        elif hire > date.today():
            report.warnings.append(RowProblem(
                index, "hire_date", str(hire), "Дата приёма в будущем"))

        termination = _parse_date(row.get("termination_date"))
        if termination is False:
            report.problems.append(RowProblem(
                index, "termination_date", row.get("termination_date", ""),
                "Дата увольнения в нераспознанном формате"))
        if isinstance(hire, date) and isinstance(termination, date) and termination < hire:
            report.problems.append(RowProblem(
                index, "termination_date", str(termination),
                "Дата увольнения раньше даты приёма"))

        balance = _parse_decimal(row.get("initial_balance_days"))
        if balance is False:
            report.problems.append(RowProblem(
                index, "initial_balance_days", row.get("initial_balance_days", ""),
                "Остаток отпуска не является числом"))
            balance = None
        elif balance is None:
            report.warnings.append(RowProblem(
                index, "initial_balance_days", "",
                "Остаток отпуска не указан — система насчитает дни с даты приёма"))
        elif balance < 0:
            report.problems.append(RowProblem(
                index, "initial_balance_days", str(balance), "Отрицательный остаток отпуска"))

        parsed.append({
            "row": index, "number": number, "full_name": full_name, "email": email,
            "hire": hire if isinstance(hire, date) else None,
            "termination": termination if isinstance(termination, date) else None,
            "balance": balance,
            "manager": (row.get("manager_employee_number") or "").strip(),
            "tags": [t.strip() for t in (row.get("extra_tags") or "").split(",") if t.strip()],
            "units": [(row.get(column) or "").strip() for column, _ in UNIT_COLUMNS],
        })

    # Руководитель должен быть либо в файле, либо уже в базе.
    known = set(seen_numbers) | set(
        Employee.objects.values_list("employee_number", flat=True))
    for item in parsed:
        if item["manager"] and item["manager"] not in known:
            report.problems.append(RowProblem(
                item["row"], "manager_employee_number", item["manager"],
                "Руководитель не найден ни в файле, ни в системе"))
        if item["manager"] == item["number"]:
            report.problems.append(RowProblem(
                item["row"], "manager_employee_number", item["manager"],
                "Сотрудник указан руководителем самого себя"))

    _detect_cycles(parsed, report)

    if report.problems:
        transaction.set_rollback(True)
        return report

    # Пробный прогон выполняет загрузку целиком и откатывает её: так
    # предпросмотр видит те же ошибки (линии подчинения, руководители
    # подразделений) и те же счётчики, что и настоящая загрузка.
    with acting_as(get_actor_for_import(), source="import"):
        report = _apply(parsed, report)
    if dry_run:
        transaction.set_rollback(True)
    return report


def get_actor_for_import():
    from audit.context import get_actor
    return get_actor()


def _apply(parsed, report):
    from employees.models import Employee, OrgUnit

    unit_cache = {}
    for item in parsed:
        parent = None
        for (_column, unit_type), name in zip(UNIT_COLUMNS, item["units"], strict=False):
            if not name:
                continue
            key = (name, unit_type, parent.id if parent else None)
            unit = unit_cache.get(key)
            if unit is None:
                unit, _ = OrgUnit.objects.get_or_create(
                    name=name, unit_type=unit_type, parent=parent)
                unit_cache[key] = unit
            parent = unit
        item["unit"] = parent

    for item in parsed:
        employee = Employee.objects.filter(employee_number=item["number"]).first()
        if employee:
            report.updated += 1
        else:
            employee = Employee(employee_number=item["number"])
            employee.set_unusable_password()
            employee.invitation_token = uuid.uuid4()
            report.created += 1

        employee.full_name = item["full_name"]
        employee.email = item["email"]
        employee.hire_date = item["hire"]
        employee.org_unit = item["unit"]
        employee.termination_date = item["termination"]
        employee.status = (Employee.Status.INACTIVE if item["termination"]
                           else Employee.Status.ACTIVE)
        employee.save()

    _assign_unit_managers(parsed, report)
    _verify_reporting_lines(parsed, report)
    if report.problems:
        transaction.set_rollback(True)
        return report

    for item in parsed:
        employee = Employee.objects.get(employee_number=item["number"])
        if item["unit"]:
            tag, _ = Tag.objects.get_or_create(name=item["unit"].name)
            EmployeeTag.objects.get_or_create(
                employee=employee, tag=tag,
                defaults={"source": Tag.Source.AUTO_ORG})
        for tag_name in item["tags"]:
            tag, _ = Tag.objects.get_or_create(name=tag_name)
            EmployeeTag.objects.get_or_create(
                employee=employee, tag=tag,
                defaults={"source": Tag.Source.MANUAL})
        if item["balance"] is not None:
            from schedule.models import VacationLedger
            VacationLedger.objects.update_or_create(
                employee=employee,
                reason=VacationLedger.Reason.INITIAL_MIGRATION,
                # Остаток задаётся на дату загрузки (ФЗ, п. 4.2.1): с этого
                # дня начисление продолжается, см. calculations.accrual_start.
                defaults={"entry_date": date.today(),
                          "days_delta": item["balance"]})

    return report


def _assign_unit_managers(parsed, report):
    """Перевод связи «сотрудник → руководитель» в «оргединица → руководитель».

    Кадровая выгрузка описывает подчинение персонально, модель хранит
    руководителя у оргединицы. Правило вывода: руководителем оргединицы
    становится тот, кого называют руководителем её сотрудники и кто сам
    состоит в этой же оргединице. Если названный руководитель сидит выше
    по структуре, оргединица остаётся без своего руководителя — поиск
    поднимется к родителю.
    """
    unit_of = {}
    for item in parsed:
        if item["unit"]:
            unit_of[item["number"]] = item["unit"]
    for employee in Employee.objects.exclude(org_unit=None):
        unit_of.setdefault(employee.employee_number, employee.org_unit)

    # Для каждой оргединицы — кого называют руководителем её сотрудники
    # (включая сотрудников вложенных оргединиц).
    declared = {}
    for item in parsed:
        if not item["manager"] or not item["unit"]:
            continue
        node = item["unit"]
        while node is not None:
            declared.setdefault(node.id, set()).add(item["manager"])
            node = node.parent

    for unit_id, manager_numbers in declared.items():
        unit = OrgUnit.objects.get(id=unit_id)
        insiders = [
            number for number in manager_numbers
            if unit_of.get(number) and unit_of[number].id == unit.id
        ]
        if not insiders:
            continue
        if len(insiders) > 1:
            names = ", ".join(sorted(insiders))
            report.warnings.append(RowProblem(
                0, "manager_employee_number", names,
                f"В оргединице «{unit.name}» несколько руководителей ({names}) — "
                f"выбран первый, проверьте оргструктуру"))
        manager = Employee.objects.filter(employee_number=sorted(insiders)[0]).first()
        if manager and unit.manager_id != manager.id:
            unit.manager = manager
            unit.save(update_fields=["manager"])


def _verify_reporting_lines(parsed, report):
    """Проверка, что подчинение из файла действительно выразилось в дереве.

    Кадровые выгрузки часто содержат подчинение «через ветвь»: сотрудник
    числится в одном отделе, а подчиняется руководителю из соседнего.
    Строгое дерево такое выразить не может — и без этой проверки сотрудник
    молча остался бы без руководителя, то есть без согласующего графика.
    """
    for item in parsed:
        if not item["manager"]:
            continue
        employee = Employee.objects.filter(employee_number=item["number"]).first()
        if employee is None:
            continue
        derived = employee.manager
        if derived is None:
            report.problems.append(RowProblem(
                item["row"], "manager_employee_number", item["manager"],
                f"Руководитель {item['manager']} не выражается оргструктурой: "
                f"он находится в другой ветви дерева. Сотрудник останется без "
                f"согласующего — поправьте оргединицы в выгрузке"))
        elif derived.employee_number != item["manager"]:
            report.problems.append(RowProblem(
                item["row"], "manager_employee_number", item["manager"],
                f"По оргструктуре руководителем окажется {derived.employee_number} "
                f"({derived.full_name}), а в файле указан {item['manager']}"))


def _detect_cycles(parsed, report):
    """Циклы в подчинении: А руководит Б, Б руководит А."""
    chain = {
        item["number"]: item["manager"]
        for item in parsed
        if item["manager"] and item["manager"] != item["number"]
    }
    rows = {item["number"]: item["row"] for item in parsed}
    for start in list(chain):
        seen, node = set(), start
        while node in chain:
            if node in seen:
                report.problems.append(RowProblem(
                    rows.get(start, 0), "manager_employee_number", start,
                    "Циклическое подчинение в оргструктуре"))
                break
            seen.add(node)
            node = chain[node]
