"""Тесты команды ensure_admin."""

import os
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase

from employees.models import Employee

STRONG_PASSWORD = "Vacation-Planner-2027!"
ENV_KEYS = ("ADMIN_EMAIL", "ADMIN_PASSWORD", "ADMIN_FULL_NAME",
            "ADMIN_EMPLOYEE_NUMBER", "ADMIN_HIRE_DATE")


class EnsureAdminTests(TestCase):
    def run_command(self, **env):
        """Запуск с заданным окружением; прочие ADMIN_* гарантированно не заданы."""
        clean = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
        clean.update(env)
        out, err = StringIO(), StringIO()
        with patch.dict(os.environ, clean, clear=True):
            call_command("ensure_admin", stdout=out, stderr=err)
        return out.getvalue() + err.getvalue()

    def assert_password_not_leaked(self, output, password=STRONG_PASSWORD):
        self.assertNotIn(password, output)

    # --- Создание -------------------------------------------------------

    def test_creates_admin_with_required_fields(self):
        output = self.run_command(ADMIN_EMAIL="admin@corp.ru",
                                  ADMIN_PASSWORD=STRONG_PASSWORD)

        admin = Employee.objects.get(email="admin@corp.ru")
        self.assertTrue(admin.is_admin)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertEqual(admin.status, Employee.Status.ACTIVE)
        self.assertEqual(admin.full_name, "Администратор")
        self.assertEqual(admin.employee_number, "ADMIN-0001")
        self.assertEqual(admin.hire_date, date.today())
        self.assertIn("создан администратор", output)
        self.assert_password_not_leaked(output)

    def test_created_admin_can_log_in(self):
        self.run_command(ADMIN_EMAIL="admin@corp.ru", ADMIN_PASSWORD=STRONG_PASSWORD)
        self.assertIsNotNone(
            authenticate(username="admin@corp.ru", password=STRONG_PASSWORD))

    def test_optional_fields_are_respected(self):
        self.run_command(ADMIN_EMAIL="admin@corp.ru", ADMIN_PASSWORD=STRONG_PASSWORD,
                         ADMIN_FULL_NAME="Гордеенко Дмитрий",
                         ADMIN_EMPLOYEE_NUMBER="000123",
                         ADMIN_HIRE_DATE="2015-03-02")
        admin = Employee.objects.get(email="admin@corp.ru")
        self.assertEqual(admin.full_name, "Гордеенко Дмитрий")
        self.assertEqual(admin.employee_number, "000123")
        self.assertEqual(admin.hire_date, date(2015, 3, 2))

    def test_password_is_not_stripped(self):
        """Пробелы по краям могут быть частью пароля."""
        password = "  " + STRONG_PASSWORD + "  "
        self.run_command(ADMIN_EMAIL="admin@corp.ru", ADMIN_PASSWORD=password)
        self.assertIsNotNone(authenticate(username="admin@corp.ru", password=password))

    # --- Пропуск --------------------------------------------------------

    def test_skips_when_nothing_is_set(self):
        output = self.run_command()
        self.assertEqual(Employee.objects.count(), 0)
        self.assertIn("пропускаем", output)

    def test_skips_with_warning_when_password_missing(self):
        output = self.run_command(ADMIN_EMAIL="admin@corp.ru")
        self.assertEqual(Employee.objects.count(), 0)
        self.assertIn("ADMIN_PASSWORD", output)

    def test_skips_with_warning_when_email_missing(self):
        output = self.run_command(ADMIN_PASSWORD=STRONG_PASSWORD)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertIn("ADMIN_EMAIL", output)
        self.assert_password_not_leaked(output)

    # --- Существующий пользователь ---------------------------------------

    def test_existing_user_is_left_untouched(self):
        """Не повышает до администратора и не меняет пароль."""
        existing = Employee.objects.create_user(
            email="ivanov@corp.ru", employee_number="100", full_name="Иванов Иван",
            hire_date=date(2020, 1, 1), password="old-password-123")

        output = self.run_command(ADMIN_EMAIL="ivanov@corp.ru",
                                  ADMIN_PASSWORD=STRONG_PASSWORD,
                                  ADMIN_FULL_NAME="Другое имя")

        existing.refresh_from_db()
        self.assertFalse(existing.is_admin)
        self.assertFalse(existing.is_superuser)
        self.assertEqual(existing.full_name, "Иванов Иван")
        self.assertTrue(existing.check_password("old-password-123"))
        self.assertFalse(existing.check_password(STRONG_PASSWORD))
        self.assertEqual(Employee.objects.count(), 1)
        self.assertIn("ничего не меняем", output)
        self.assert_password_not_leaked(output)

    def test_existing_user_matched_case_insensitively(self):
        Employee.objects.create_user(
            email="ivanov@corp.ru", employee_number="100", full_name="Иванов Иван",
            hire_date=date(2020, 1, 1))
        self.run_command(ADMIN_EMAIL="Ivanov@Corp.RU", ADMIN_PASSWORD=STRONG_PASSWORD)
        self.assertEqual(Employee.objects.count(), 1)

    def test_repeated_run_is_idempotent(self):
        """Каждый перезапуск контейнера вызывает команду заново."""
        env = {"ADMIN_EMAIL": "admin@corp.ru", "ADMIN_PASSWORD": STRONG_PASSWORD}
        self.run_command(**env)
        first = Employee.objects.get(email="admin@corp.ru")
        self.run_command(**env)
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(Employee.objects.get(email="admin@corp.ru").password,
                         first.password)

    def test_concurrent_creation_is_not_an_error(self):
        """Две реплики стартуют разом: вторая видит, что первая уже создала."""
        Employee.objects.create_user(
            email="admin@corp.ru", employee_number="X1", full_name="Соседняя реплика",
            hire_date=date(2020, 1, 1))
        with patch.object(Employee.objects, "filter") as fake_filter, \
             patch.object(Employee.objects, "create_superuser",
                          side_effect=IntegrityError("duplicate key")):
            # Первая проверка «нет пользователя», затем — «уже есть».
            fake_filter.return_value.exists.side_effect = [False, False, True]
            output = self.run_command(ADMIN_EMAIL="admin@corp.ru",
                                      ADMIN_PASSWORD=STRONG_PASSWORD)
        self.assertIn("параллельным процессом", output)

    # --- Ошибки конфигурации ---------------------------------------------

    def test_weak_password_rejected(self):
        weak = "12345678"
        with self.assertRaises(CommandError) as ctx:
            self.run_command(ADMIN_EMAIL="admin@corp.ru", ADMIN_PASSWORD=weak)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertIn("проверку надёжности", str(ctx.exception))
        self.assertNotIn(weak, str(ctx.exception))

    def test_password_similar_to_email_rejected(self):
        with self.assertRaises(CommandError):
            self.run_command(ADMIN_EMAIL="administrator@corp.ru",
                             ADMIN_PASSWORD="administrator")
        self.assertEqual(Employee.objects.count(), 0)

    def test_employee_number_conflict(self):
        Employee.objects.create_user(
            email="someone@corp.ru", employee_number="ADMIN-0001",
            full_name="Занявший номер", hire_date=date(2020, 1, 1))
        with self.assertRaises(CommandError) as ctx:
            self.run_command(ADMIN_EMAIL="admin@corp.ru", ADMIN_PASSWORD=STRONG_PASSWORD)
        self.assertIn("ADMIN_EMPLOYEE_NUMBER", str(ctx.exception))
        self.assertFalse(Employee.objects.filter(email="admin@corp.ru").exists())
        self.assert_password_not_leaked(str(ctx.exception))

    def test_bad_hire_date(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_command(ADMIN_EMAIL="admin@corp.ru", ADMIN_PASSWORD=STRONG_PASSWORD,
                             ADMIN_HIRE_DATE="02.03.2015")
        self.assertIn("ГГГГ-ММ-ДД", str(ctx.exception))
        self.assertEqual(Employee.objects.count(), 0)


class EntrypointOrderTests(TestCase):
    """Команда вызывается после миграций и до запуска gunicorn."""

    def test_order_in_entrypoint(self):
        script = (Path(__file__).resolve().parent.parent / "entrypoint.sh").read_text(
            encoding="utf-8")
        commands = [line.strip() for line in script.splitlines()
                    if line.strip() and not line.strip().startswith("#")]
        position = {
            name: next(i for i, line in enumerate(commands) if marker in line)
            for name, marker in [("migrate", "manage.py migrate"),
                                 ("ensure_admin", "manage.py ensure_admin"),
                                 ("gunicorn", "exec gunicorn")]
        }
        self.assertLess(position["migrate"], position["ensure_admin"])
        self.assertLess(position["ensure_admin"], position["gunicorn"])

    def test_ensure_admin_not_skipped_with_migrations(self):
        """Вызов вне блока RUN_MIGRATIONS: в Kubernetes миграции идут отдельным Job."""
        script = (Path(__file__).resolve().parent.parent / "entrypoint.sh").read_text(
            encoding="utf-8")
        block = script[script.index('if [ "${RUN_MIGRATIONS'):script.index("fi")]
        self.assertNotIn("ensure_admin", block)

    def test_no_shell_tracing(self):
        """set -x напечатал бы окружение команд в лог."""
        script = (Path(__file__).resolve().parent.parent / "entrypoint.sh").read_text(
            encoding="utf-8")
        self.assertNotRegex(script, r"(?m)^\s*set\s+-[a-z]*x")
