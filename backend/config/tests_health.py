"""Тесты эндпоинта живости."""

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient


class HealthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_available_without_authentication(self):
        """Проверяющему незачем знать учётные данные."""
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["database"], "ok")

    def test_reports_degraded_when_database_is_down(self):
        """Недоступная база даёт понятный 503, а не стек 500."""
        with patch("config.health.connection") as fake:
            fake.cursor.side_effect = OSError("could not connect to server")
            response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["database"], "unavailable")
        self.assertNotIn("detail", body)

    def test_error_text_goes_to_log_not_response(self):
        """В тексте ошибки бывают хост и пользователь базы: наружу его нельзя."""
        with patch("config.health.connection") as fake, \
                self.assertLogs("config.health", "WARNING") as logs:
            fake.cursor.side_effect = OSError("host=db.internal user=vacation")
            response = self.client.get("/api/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("db.internal", response.content.decode())
        self.assertIn("db.internal", logs.output[0])

    def test_live_does_not_touch_database(self):
        """Liveness не зависит от базы: иначе её сбой перезапускал бы поды."""
        with patch("config.health.connection") as fake:
            fake.cursor.side_effect = OSError("down")
            response = self.client.get("/api/health/live/")
        self.assertEqual(response.status_code, 200)
        fake.cursor.assert_not_called()

    def test_ready_alias(self):
        self.assertEqual(self.client.get("/api/health/ready/").json()["database"], "ok")
