"""Разбор адреса базы данных из переменной окружения."""

from django.test import SimpleTestCase

from config.settings import _database_from_url


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
