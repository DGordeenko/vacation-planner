"""Разбор адреса базы данных из переменной окружения."""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from config.checks import check_database_tls
from config.settings import DEMO_SQLITE_PATH, _database_from_url, _demo_database


class DatabaseUrlTests(SimpleTestCase):
    def test_sqlite_relative_path(self):
        config = _database_from_url("sqlite:///db.sqlite3")
        self.assertEqual(config["NAME"], "db.sqlite3")

    def test_sqlite_absolute_path(self):
        """Четыре слеша — абсолютный путь. Раньше он становился относительным."""
        config = _database_from_url("sqlite:////tmp/demo.sqlite3")
        self.assertEqual(config["NAME"], "/tmp/demo.sqlite3")

    def test_postgres(self):
        config = _database_from_url("postgres://user:p%40ss@db:5433/vacation")
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual((config["USER"], config["PASSWORD"], config["HOST"],
                          config["PORT"], config["NAME"]),
                         ("user", "p@ss", "db", "5433", "vacation"))

    def test_postgres_tls_params(self):
        config = _database_from_url(
            "postgres://u:p@h1,h2:6432/db?sslmode=verify-full"
            "&sslrootcert=/app/certs/root.crt&target_session_attrs=read-write&connect_timeout=5")
        self.assertEqual(config["OPTIONS"], {
            "sslmode": "verify-full", "sslrootcert": "/app/certs/root.crt",
            "target_session_attrs": "read-write", "connect_timeout": "5"})

    def test_no_query_no_options(self):
        """Без параметров OPTIONS не появляется: локальный запуск как раньше."""
        with mock.patch.dict("os.environ", {"DB_SSLMODE": "", "DB_SSLROOTCERT": ""}):
            self.assertNotIn("OPTIONS", _database_from_url("postgres://u:p@db/x"))

    def test_env_overrides_url(self):
        env = {"DB_SSLMODE": "verify-full", "DB_SSLROOTCERT": "/certs/ca.crt"}
        with mock.patch.dict("os.environ", env):
            config = _database_from_url("postgres://u:p@db/x?sslmode=prefer")
        self.assertEqual(config["OPTIONS"],
                         {"sslmode": "verify-full", "sslrootcert": "/certs/ca.crt"})

    def test_unknown_param_rejected(self):
        with self.assertRaises(ValueError):
            _database_from_url("postgres://u:p@db/x?sslmod=require")

    def test_bad_sslmode_rejected(self):
        with self.assertRaises(ValueError):
            _database_from_url("postgres://u:p@db/x?sslmode=on")

    def test_bad_timeout_rejected(self):
        with self.assertRaises(ValueError):
            _database_from_url("postgres://u:p@db/x?connect_timeout=abc")

    def test_unknown_scheme_rejected(self):
        with self.assertRaises(ValueError):
            _database_from_url("mysql://db/vacation")


class DemoDatabaseTests(SimpleTestCase):
    def test_disabled_demo_has_no_file(self):
        """Выключенное демо не создаёт файлов: проверки Django открывают все базы."""
        self.assertEqual(_demo_database(False, "")["NAME"], ":memory:")
        self.assertEqual(_demo_database(False, "postgres://u:p@db/demo")["NAME"], ":memory:")

    def test_enabled_demo_defaults_to_temp_directory(self):
        import tempfile
        config = _demo_database(True, "")
        self.assertEqual(config["NAME"], DEMO_SQLITE_PATH)
        self.assertEqual(str(DEMO_SQLITE_PATH.parent), tempfile.gettempdir())

    def test_enabled_demo_uses_url(self):
        self.assertEqual(_demo_database(True, "postgres://u:p@db/demo")["NAME"], "demo")


def _ids(**db):
    base = {"ENGINE": "django.db.backends.postgresql", "NAME": "x"}
    with override_settings(DATABASES={"default": {**base, **db}}):
        return {m.id for m in check_database_tls(None)}


class DatabaseTlsCheckTests(SimpleTestCase):
    def test_warns_without_verify_full(self):
        self.assertIn("vacation.W001", _ids(OPTIONS={"sslmode": "require"}))

    def test_quiet_with_verify_full(self):
        self.assertNotIn("vacation.W001", _ids(OPTIONS={"sslmode": "verify-full"}))
