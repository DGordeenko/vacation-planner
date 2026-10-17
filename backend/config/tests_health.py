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
        self.assertIn("could not connect", body["detail"])
