"""Справочник причин внепланового отсутствия в рабочей базе.

Как и правила проверки до schedule/0006, причины создавала только команда
seed_demo (с учётками с известным паролем) и загрузка демо. В рабочей базе
справочник был пуст, а причина обязательна, — отсутствие нельзя было
зафиксировать вовсе. Существующие причины не меняются: администратор мог
их переименовать.
"""

from django.db import migrations

REASONS = [
    ("sick_leave", "Больничный", False),
    ("unpaid_leave", "Отпуск за свой счёт", False),
    ("family_reasons", "Семейные обстоятельства", False),
    ("public_duty", "Государственные обязанности", False),
    ("absence_no_reason", "Отсутствие без уважительной причины", False),
    ("other", "Другое", True),
]


def create_reasons(apps, schema_editor):
    AbsenceReason = apps.get_model("company", "AbsenceReason")
    for code, name, is_other in REASONS:
        AbsenceReason.objects.get_or_create(
            code=code, defaults={"name": name, "is_other": is_other})


class Migration(migrations.Migration):

    dependencies = [
        ("company", "0003_companysettings_workday_hours_and_more"),
    ]

    operations = [
        migrations.RunPython(create_reasons, migrations.RunPython.noop),
    ]
