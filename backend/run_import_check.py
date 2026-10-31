import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from company.models import CompanySettings
from employees.importers import import_employees
from employees.models import Employee, EmployeeTag, OrgUnit

CompanySettings.get_solo()

def run(path, title, dry_run=False):
    print(f"\n{'='*66}\n{title}\n{'-'*66}")
    with open(path, 'rb') as f:
        report = import_employees(f.read(), dry_run=dry_run)
    print(f"строк в файле: {report.total_rows} | применено: {'ДА' if report.applied and not dry_run else 'НЕТ'}")
    if report.applied and not dry_run:
        print(f"создано: {report.created} | обновлено: {report.updated}")
    for p in report.problems:
        loc = f"строка {p.row}" if p.row else "файл"
        print(f"  ОШИБКА  [{loc}] {p.column}: {p.message}" + (f" — «{p.value}»" if p.value else ""))
    for w in report.warnings:
        print(f"  внимание [строка {w.row}] {w.column}: {w.message}")
    return report

run('testdata/01_clean.csv', 'НАБОР 1. Чистая выгрузка, ожидаемый формат')
print(f"  → сотрудников в базе: {Employee.objects.count()}, оргединиц: {OrgUnit.objects.count()}")
for u in OrgUnit.objects.all().order_by('unit_type','name'):
    print(f"     {u.get_unit_type_display():16} {u.name:24} рук.: {u.manager.full_name if u.manager else '—'}")

run('testdata/02_messy.csv', 'НАБОР 2. Русские заголовки, запятая-разделитель, разные форматы дат')
for e in Employee.objects.filter(employee_number__startswith='200'):
    print(f"  → {e.employee_number} {e.full_name:32} принят {e.hire_date}")

run('testdata/03_broken.csv', 'НАБОР 3. Дефектные данные (файл не должен примениться)')
print(f"  → сотрудников с номером 30xx в базе: {Employee.objects.filter(employee_number__startswith='300').count()} (ожидается 0)")

run('testdata/04_cycle.csv', 'НАБОР 4. Циклическое подчинение')
run('testdata/05_cp1251.csv', 'НАБОР 5. Кодировка CP1251')
for e in Employee.objects.filter(employee_number='5001'):
    print(f"  → прочитано: {e.full_name}")
run('testdata/06_no_columns.csv', 'НАБОР 6. Посторонний файл без нужных колонок')

print(f"\n{'='*66}\nПОВТОРНАЯ ЗАГРУЗКА НАБОРА 1 (проверка обновления, не дублирования)")
before = Employee.objects.count()
r = run('testdata/01_clean.csv', 'НАБОР 1 повторно')
print(f"  → было {before}, стало {Employee.objects.count()} — дублей нет: {before == Employee.objects.count()}")

print(f"\n{'='*66}\nПРЕДПРОСМОТР (dry_run) — проверка без записи")
before = Employee.objects.count()
run('testdata/02_messy.csv', 'Предпросмотр набора 2', dry_run=True)
print(f"  → записей в базе не изменилось: {before == Employee.objects.count()}")

print(f"\n{'='*66}\nТЕГИ, назначенные автоматически и вручную")
for et in EmployeeTag.objects.select_related('employee','tag').order_by('employee__employee_number')[:8]:
    print(f"  {et.employee.employee_number} {et.employee.full_name:30} {et.tag.name:22} ({et.get_source_display()})")
