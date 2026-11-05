"""Журналы: без персональных данных и токенов, стек ошибок — не клиенту."""

import json
import logging

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path
from rest_framework.test import APIClient

from config.logs import JsonFormatter, MaskPersonalDataFilter, mask


def _record(msg, *args, exc_info=None, **extra):
    record = logging.LogRecord("t", logging.WARNING, __file__, 1, msg, args, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class MaskTests(SimpleTestCase):
    def test_email_masked(self):
        self.assertEqual(mask("письмо ivan.petrov@corp.ru не ушло"), "письмо i***@corp.ru не ушло")

    def test_tokens_in_urls_masked(self):
        self.assertEqual(mask("/?invite=3f2a-11&tab=1 /cb?code=xyz&state=abc"),
                         "/?invite=***&tab=1 /cb?code=***&state=***")

    def test_filter_masks_arguments_and_traceback(self):
        """Почта в аргументах и в тексте исключения — тоже маскируется."""
        try:
            raise ValueError("нет пользователя boss@corp.ru")
        except ValueError:
            import sys
            record = _record("вход %s", "anna@corp.ru", exc_info=sys.exc_info())
        MaskPersonalDataFilter().filter(record)
        self.assertEqual(record.getMessage(), "вход a***@corp.ru")
        self.assertNotIn("boss@corp.ru", record.exc_text)


class JsonFormatterTests(SimpleTestCase):
    def test_one_json_line_with_fields(self):
        line = JsonFormatter().format(_record("событие %s", 1, event="login_failed"))
        self.assertNotIn("\n", line)
        entry = json.loads(line)
        self.assertEqual((entry["level"], entry["message"], entry["event"]),
                         ("WARNING", "событие 1", "login_failed"))


def _boom(request):
    raise RuntimeError("секрет в стеке: db-password")


urlpatterns = [path("boom/", _boom)]


@override_settings(ROOT_URLCONF="config.tests_logs", DEBUG=False)
class ServerErrorTests(SimpleTestCase):
    def test_500_has_no_traceback(self):
        """Стек и текст исключения — только в журнал, клиенту — нет."""
        self.client.raise_request_exception = False
        with self.assertLogs("django.request", "ERROR"):
            response = self.client.get("/boom/")
        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertNotIn("db-password", body)
        self.assertNotIn("Traceback", body)


class SecurityLogTests(TestCase):
    def test_failed_login_logged_with_masked_email(self):
        from django.core.cache import cache
        cache.clear()
        with self.assertLogs("security", "WARNING") as logs:
            APIClient().post("/api/v1/auth/login/",
                             {"email": "ivan@corp.ru", "password": "bad"}, format="json")
        self.assertIn("i***@corp.ru", logs.output[0])
        self.assertNotIn("ivan@corp.ru", logs.output[0])
        cache.clear()
