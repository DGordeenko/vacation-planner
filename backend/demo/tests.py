"""Демонстрационный режим.

Главное, что здесь проверяется, — изоляция: демо-посетитель с правами
администратора не должен ни увидеть, ни изменить рабочие данные,
а рабочий администратор — наткнуться на вымышленных сотрудников.
"""

import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

from django.db import connections, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from company.models import CompanySettings, Holiday
from demo import data
from demo.db import DEMO_ALIAS, is_demo_active, use_demo_database
from demo.loader import DemoLoadError, load
from employees.models import Employee
from mailer.models import OutgoingEmail
from schedule.models import ApprovalStep, Notification, ScheduleEntry
from schedule.tests import seed_rules

DEMO_ON = dict(DEMO_MODE_ENABLED=True)
MAIL_ON = dict(
    MAIL_NOTIFICATIONS_ENABLED=True,
    APP_BASE_URL="https://vacation.corp.ru",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


def make_employee(number, email, **extra):
    return Employee.objects.create_user(
        email=email, employee_number=number, full_name=f"Сотрудник {number}",
        hire_date=date(2020, 1, 1), **extra)


@override_settings(**DEMO_ON)
class IsolationTests(TestCase):
    databases = {"default", DEMO_ALIAS}

    def setUp(self):
        CompanySettings.get_solo()
        seed_rules()
        self.prod_admin = make_employee("P-1", "admin@corp.ru", is_admin=True)
        self.prod_entry = ScheduleEntry.objects.create(
            employee=self.prod_admin, created_by=self.prod_admin,
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from=date(2027, 3, 1), date_to=date(2027, 3, 7))
        self.prod_token = Token.objects.create(user=self.prod_admin)

        with use_demo_database():
            CompanySettings.get_solo()
            seed_rules()
            # Табельный номер совпадает с рабочим намеренно: базы разные,
            # и пересечение ключей не должно ни на что влиять.
            self.demo_admin = make_employee(
                data.ROLES_BY_CODE["admin"].employee_number, "demo@test-corp.example",
                is_admin=True)
            self.demo_employee = make_employee(
                data.ROLES_BY_CODE["employee"].employee_number, "e@test-corp.example")
            make_employee(data.ROLES_BY_CODE["manager"].employee_number,
                          "m@test-corp.example")

        self.client = APIClient()

    def demo_login(self, role="admin"):
        response = self.client.post("/api/v1/auth/demo-login/", {"role": role},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["scheme"], "Demo")
        self.client.credentials(HTTP_AUTHORIZATION=f"Demo {body['token']}")
        return body

    def test_login_by_role_without_password(self):
        body = self.demo_login("employee")
        self.assertEqual(body["employee_id"], str(self.demo_employee.id))
        me = self.client.get("/api/v1/auth/me/").json()
        self.assertEqual(me["email"], "e@test-corp.example")

    def test_unknown_role_rejected(self):
        response = self.client.post("/api/v1/auth/demo-login/", {"role": "root"},
                                    format="json")
        self.assertEqual(response.status_code, 400)

    def test_demo_admin_does_not_see_production_entries(self):
        self.demo_login("admin")
        body = self.client.get("/api/v1/schedule-entries/").json()
        ids = {row["id"] for row in body["results"]}
        self.assertNotIn(str(self.prod_entry.id), ids)

    def test_production_admin_does_not_see_demo_employees(self):
        with use_demo_database():
            ScheduleEntry.objects.create(
                employee=self.demo_employee, created_by=self.demo_employee,
                record_type=ScheduleEntry.RecordType.VACATION,
                date_from=date(2027, 5, 3), date_to=date(2027, 5, 9))
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.prod_token.key}")
        body = self.client.get("/api/v1/schedule-entries/").json()
        self.assertEqual({row["id"] for row in body["results"]}, {str(self.prod_entry.id)})
        self.assertFalse(Employee.objects.filter(email__endswith="test-corp.example").exists())

    def test_demo_writes_go_to_demo_database(self):
        self.demo_login("employee")
        response = self.client.post("/api/v1/schedule-entries/", {
            "record_type": "vacation", "date_from": "2027-05-03", "date_to": "2027-05-09",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(ScheduleEntry.objects.count(), 1)  # только рабочая запись
        with use_demo_database():
            self.assertEqual(ScheduleEntry.objects.count(), 1)
            self.assertEqual(ScheduleEntry.objects.get().employee, self.demo_employee)

    def test_demo_token_is_not_accepted_as_production_token(self):
        body = self.demo_login("admin")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {body['token']}")
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)

    def test_production_token_is_not_accepted_as_demo_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Demo {self.prod_token.key}")
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)

    @override_settings(DEMO_MODE_ENABLED=False)
    def test_disabled_demo_login_is_not_found(self):
        response = self.client.post("/api/v1/auth/demo-login/", {"role": "admin"},
                                    format="json")
        self.assertEqual(response.status_code, 404)

    def test_disabled_demo_rejects_issued_tokens(self):
        body = self.demo_login("admin")
        with override_settings(DEMO_MODE_ENABLED=False):
            self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)
        self.assertEqual(body["role"], "admin")

    def test_auth_methods_lists_roles(self):
        demo = self.client.get("/api/v1/auth/methods/").json()["demo"]
        self.assertTrue(demo["enabled"])
        self.assertEqual([r["code"] for r in demo["roles"]],
                         ["employee", "manager", "admin"])
        self.assertTrue(all(r["full_name"] and r["description"] for r in demo["roles"]))

    @override_settings(DEMO_MODE_ENABLED=False)
    def test_auth_methods_hides_disabled_demo(self):
        demo = self.client.get("/api/v1/auth/methods/").json()["demo"]
        self.assertEqual(demo, {"enabled": False, "roles": []})

    def test_auth_methods_hides_demo_without_role_employees(self):
        with use_demo_database():
            Employee.objects.update(status=Employee.Status.INACTIVE)
        demo = self.client.get("/api/v1/auth/methods/").json()["demo"]
        self.assertFalse(demo["enabled"])
        response = self.client.post("/api/v1/auth/demo-login/", {"role": "admin"},
                                    format="json")
        self.assertEqual(response.status_code, 503)

    def test_transaction_rollback_applies_to_demo_database(self):
        """atomic() без using в демо-запросе откатывает демо-базу, а не рабочую."""
        with use_demo_database():
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    Holiday.objects.create(date=date(2027, 1, 1), name="Тест",
                                           day_type=Holiday.DayType.HOLIDAY)
                    raise RuntimeError
            self.assertFalse(Holiday.objects.exists())

    def test_switch_is_restored_after_error(self):
        with self.assertRaises(RuntimeError):
            with use_demo_database():
                raise RuntimeError
        self.assertFalse(is_demo_active())
        self.assertIsNot(connections["default"], connections[DEMO_ALIAS])
        self.assertTrue(Employee.objects.filter(employee_number="P-1").exists())

    @override_settings(**MAIL_ON)
    def test_no_mail_queued_in_demo(self):
        """Адреса в демо вымышленные: уведомление в ленте есть, письма нет."""
        with use_demo_database():
            Notification.objects.create(
                recipient=self.demo_employee,
                notification_type=Notification.NotificationType.APPROVAL_REQUIRED,
                message="Тест")
            self.assertEqual(Notification.objects.count(), 1)
            self.assertEqual(OutgoingEmail.objects.count(), 0)
        self.assertEqual(OutgoingEmail.objects.count(), 0)

    def test_load_refuses_when_demo_points_to_production(self):
        production = connections["default"].settings_dict
        with mock.patch("demo.db.settings") as fake_settings:
            fake_settings.DATABASES = {"default": production, DEMO_ALIAS: dict(production)}
            with self.assertRaises(DemoLoadError):
                load()
        self.assertTrue(Employee.objects.filter(employee_number="P-1").exists())


@override_settings(**DEMO_ON)
class LoaderTests(TestCase):
    """Полная загрузка выполняется один раз на класс: она занимает секунды."""

    databases = {"default", DEMO_ALIAS}

    @classmethod
    def setUpTestData(cls):
        cls.production_employee = make_employee("P-1", "admin@corp.ru")
        cls.report = load()

    def test_staff_loaded_into_demo_only(self):
        self.assertEqual(self.report.employees, 157)
        self.assertEqual(list(Employee.objects.values_list("employee_number", flat=True)),
                         ["P-1"])
        self.assertFalse(Holiday.objects.exists())
        with use_demo_database():
            self.assertEqual(Employee.objects.count(), 157)
            self.assertEqual(CompanySettings.get_solo().organization_name,
                             data.ORGANIZATION_NAME)

    def test_team_plans_pass_validation(self):
        """Заданные вручную отпуска команды проходят проверки — иначе сценарий
        демо молча распадётся после изменения правил."""
        self.assertEqual(self.report.skipped_periods, [])

    def test_all_statuses_present(self):
        self.assertGreater(self.report.approved, 0)
        self.assertGreater(self.report.pending, 0)
        self.assertGreater(self.report.drafts, 0)

    def test_roles(self):
        with use_demo_database():
            admin = Employee.objects.get(
                employee_number=data.ROLES_BY_CODE["admin"].employee_number)
            self.assertTrue(admin.is_admin)
            manager = Employee.objects.get(
                employee_number=data.ROLES_BY_CODE["manager"].employee_number)
            employee = Employee.objects.get(
                employee_number=data.ROLES_BY_CODE["employee"].employee_number)
            self.assertEqual(employee.manager, manager)
            self.assertEqual(ApprovalStep.objects.filter(
                approver=manager, status="pending", approval__status="pending").count(), 2)

    def test_tag_limit_scenario(self):
        """Неделя 12–18 июля уже занята двумя коллегами по дежурной смене."""
        from schedule.calculations import validate_entry
        with use_demo_database():
            employee = Employee.objects.get(
                employee_number=data.ROLES_BY_CODE["employee"].employee_number)
            result = validate_entry(employee, date(2027, 7, 12), date(2027, 7, 18),
                                    ScheduleEntry.RecordType.VACATION)
            self.assertEqual([v.code for v in result.violations], ["tag_limit_exceeded"])

    def test_initial_balances_from_export(self):
        """Баланс на сегодня равен остатку из выгрузки (сценарий B4)."""
        from schedule.calculations import balance_on
        with use_demo_database():
            employee = Employee.objects.get(employee_number="10001")
            self.assertEqual(str(balance_on(employee, date.today())), "5.75")

    def test_reload_resets_changes(self):
        with use_demo_database():
            Employee.objects.filter(employee_number="10002").update(full_name="Изменено")
        load()
        with use_demo_database():
            self.assertNotEqual(
                Employee.objects.get(employee_number="10002").full_name, "Изменено")
            self.assertEqual(Employee.objects.count(), 157)
        self.assertEqual(Employee.objects.count(), 1)


class EntrypointTests(SimpleTestCase):
    """Загрузка демо при старте контейнера. Вместо python и gunicorn —
    заглушки, которые только записывают, с чем их вызвали. Заглушка python
    завершается ошибкой, если в аргументах есть FAIL_ON."""

    def run_entrypoint(self, fail_on="", args=(), **env):
        script = Path(__file__).resolve().parent.parent / "entrypoint.sh"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.log"
            for name in ("python", "gunicorn"):
                stub = Path(tmp) / name
                stub.write_text(
                    "#!/bin/sh\n"
                    f'echo "{name} $*" >> "{log}"\n'
                    f'case "$*" in *"{fail_on or "<never>"}"*) exit 1 ;; esac\n')
                stub.chmod(0o755)
            result = subprocess.run(
                ["sh", str(script), *args], capture_output=True, text=True,
                env={"PATH": f"{tmp}:/usr/bin:/bin", "SECRET_KEY": "test-secret", **env})
            self.stderr = result.stderr
            self.returncode = result.returncode
            return log.read_text().splitlines() if log.exists() else []

    def gunicorn_started(self, calls):
        return any(call.startswith("gunicorn") for call in calls)

    def test_demo_loaded_before_gunicorn_when_enabled(self):
        calls = self.run_entrypoint(DEMO_MODE_ENABLED="True")
        self.assertIn("python manage.py migrate --database=demo --noinput", calls)
        self.assertLess(calls.index("python manage.py load_demo"),
                        next(i for i, c in enumerate(calls) if c.startswith("gunicorn")))

    def test_demo_skipped_by_default(self):
        """Без явного DEMO_MODE_ENABLED демо не трогается вовсе."""
        calls = self.run_entrypoint()
        self.assertFalse(any("demo" in call for call in calls))
        self.assertTrue(self.gunicorn_started(calls))

    def test_demo_load_can_be_moved_to_job(self):
        calls = self.run_entrypoint(DEMO_MODE_ENABLED="1", DEMO_LOAD_ON_START="0",
                                    DEMO_RESET_DAILY="0")
        self.assertFalse(any("demo" in call for call in calls))

    def test_daily_reset_started_with_demo(self):
        """Ежедневный сброс — фоновый процесс рядом с gunicorn, только при демо."""
        calls = self.run_entrypoint(DEMO_MODE_ENABLED="True")
        self.assertIn("python manage.py demo_reset_daily", calls)
        self.assertTrue(self.gunicorn_started(calls))
        self.assertNotIn("python manage.py demo_reset_daily", self.run_entrypoint())
        self.assertNotIn("python manage.py demo_reset_daily",
                         self.run_entrypoint(DEMO_MODE_ENABLED="True", DEMO_RESET_DAILY="0"))

    def test_failed_demo_migration_does_not_stop_start(self):
        """Ровно сбой с Railway: миграция демо-базы падает, сервис стартует."""
        calls = self.run_entrypoint(fail_on="--database=demo", DEMO_MODE_ENABLED="True")
        self.assertTrue(self.gunicorn_started(calls))
        self.assertNotIn("python manage.py load_demo", calls)
        self.assertIn("демо-режим недоступен", self.stderr)

    def test_failed_demo_load_does_not_stop_start(self):
        calls = self.run_entrypoint(fail_on="load_demo", DEMO_MODE_ENABLED="True")
        self.assertTrue(self.gunicorn_started(calls))
        self.assertIn("демо-режим недоступен", self.stderr)

    def test_failed_main_migration_still_stops_start(self):
        """Рабочая база — не витрина: без неё сервис запускать нельзя."""
        calls = self.run_entrypoint(fail_on="migrate --noinput", DEMO_MODE_ENABLED="True")
        self.assertFalse(self.gunicorn_started(calls))
        self.assertNotEqual(self.returncode, 0)


class StartupResilienceTests(SimpleTestCase):
    """Демо не должно мешать сборке образа и запуску рабочей системы.

    manage.py check при сборке образа не должен оставлять файл демо-базы.

    Причина сбоя на Railway: проверка выполнялась от root, Django 6.1
    подключался к файловой демо-базе и создавал её. В образе оставался
    пустой файл root, и пользователь app не мог в него писать.
    """

    def test_check_does_not_create_demo_database(self):
        backend = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("DEMO_", "DATABASE_URL"))}
            env.update(TMPDIR=tmp, SECRET_KEY="build-only",
                       DATABASE_URL=f"sqlite:///{tmp}/main.sqlite3")
            subprocess.run([sys.executable, "manage.py", "check"], cwd=backend,
                           env=env, check=True, capture_output=True)
            self.assertFalse((Path(tmp) / "vacation-planner-demo.sqlite3").exists())

    def test_startup_commands_ignore_unreachable_demo_database(self):
        """Команды старта и расписания не падают, если демо-база недоступна:
        Django 6.1 перед командой проверяет модели на всех базах."""
        backend = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("DEMO_", "DATABASE_URL", "ADMIN_"))}
            env.update(SECRET_KEY="x", DATABASE_URL=f"sqlite:///{tmp}/main.sqlite3",
                       DEMO_MODE_ENABLED="True",
                       DEMO_DATABASE_URL=f"sqlite:///{tmp}/missing-dir/demo.sqlite3")
            for command in (["migrate", "--noinput"], ["ensure_admin"], ["send_mail_queue"]):
                result = subprocess.run([sys.executable, "manage.py", *command],
                                        cwd=backend, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{command}: {result.stderr[-500:]}")


class DailyResetScheduleTests(SimpleTestCase):
    """Ежедневный сброс демо: когда следующий запуск."""

    def setUp(self):
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo("Europe/Moscow")

    def test_next_midnight_local_time(self):
        from datetime import datetime, time, timezone

        from demo.schedule import next_run
        # 22:30 UTC 31.10 = 01:30 МСК 01.11 → следующая полночь 02.11 по Москве
        now = datetime(2026, 10, 31, 22, 30, tzinfo=timezone.utc)
        moment = next_run(now, time(0, 0), self.tz)
        self.assertEqual((moment.day, moment.hour, moment.minute), (2, 0, 0))
        # 20:00 UTC 31.10 = 23:00 МСК → полночь через час
        moment = next_run(datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc), time(0, 0), self.tz)
        self.assertEqual((moment.month, moment.day), (11, 1))

    def test_exact_moment_moves_to_next_day(self):
        from datetime import datetime, time

        from demo.schedule import next_run
        now = datetime(2026, 11, 1, 0, 0, tzinfo=self.tz)
        self.assertEqual(next_run(now, time(0, 0), self.tz).day, 2)

    def test_reset_time_parsing(self):
        from datetime import time

        from demo.schedule import parse_reset_time
        self.assertEqual(parse_reset_time("03:30"), time(3, 30))
        with self.assertRaises(ValueError):
            parse_reset_time("полночь")


class EntrypointCommandsTests(SimpleTestCase):
    """Команда вместо веб-сервера (cron-сервис Railway, CronJob) и почта."""

    # Те же заглушки, что в EntrypointTests, без повторного запуска его тестов.
    run_entrypoint = EntrypointTests.run_entrypoint
    gunicorn_started = EntrypointTests.gunicorn_started

    def test_command_runs_without_migrations_or_gunicorn(self):
        calls = self.run_entrypoint(args=("python", "manage.py", "send_mail_queue"))
        self.assertEqual(calls, ["python manage.py send_mail_queue"])

    def test_mail_scheduler_only_when_mail_enabled(self):
        self.assertNotIn("python manage.py mail_scheduler", self.run_entrypoint())
        calls = self.run_entrypoint(MAIL_NOTIFICATIONS_ENABLED="True")
        self.assertIn("python manage.py mail_scheduler", calls)
        self.assertTrue(self.gunicorn_started(calls))
        self.assertNotIn("python manage.py mail_scheduler", self.run_entrypoint(
            MAIL_NOTIFICATIONS_ENABLED="True", MAIL_SCHEDULER="0"))


class EntrypointSecretKeyTests(SimpleTestCase):
    """Без своего SECRET_KEY сервис не стартует."""

    run_entrypoint = EntrypointTests.run_entrypoint

    def test_refuses_default_key(self):
        for key in ("", "dev-only-insecure-key-replace-in-production",
                    "замените-на-случайное-значение"):
            with self.subTest(key=key):
                calls = self.run_entrypoint(SECRET_KEY=key)
                self.assertEqual(calls, [])
                self.assertEqual(self.returncode, 1)
                self.assertIn("SECRET_KEY", self.stderr)

    def test_local_override(self):
        calls = self.run_entrypoint(SECRET_KEY="", ALLOW_INSECURE_SECRET_KEY="1")
        self.assertTrue(any(call.startswith("gunicorn") for call in calls))
