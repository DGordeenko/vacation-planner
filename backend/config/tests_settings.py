"""Разбор адреса базы данных из переменной окружения."""

from django.test import SimpleTestCase

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
