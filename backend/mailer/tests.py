"""Тесты почтовых уведомлений."""

from datetime import date
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from company.models import CompanySettings
from employees.models import Employee, OrgUnit
from mailer.models import OutgoingEmail
from mailer.sending import send_digest, send_urgent
from schedule.models import Notification

MAIL_ON = dict(
    MAIL_NOTIFICATIONS_ENABLED=True,
    APP_BASE_URL="https://vacation.corp.ru",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="vacation@corp.ru",
)


class MailerBase(TestCase):
    def setUp(self):
        CompanySettings.get_solo()
        self.client = APIClient()
        unit = OrgUnit.objects.create(
            name="Отдел М", unit_type=OrgUnit.UnitType.DEPARTMENT)
        self.employee = Employee.objects.create_user(
            email="user@corp.ru", employee_number="M1", full_name="Пользователь",
            hire_date=date(2020, 1, 1), org_unit=unit, password="pw12345")
        mail.outbox = []

    def notify(self, kind, message="Сообщение", recipient=None):
        return Notification.objects.create(
            recipient=recipient or self.employee,
            notification_type=kind, message=message)


class QueueingTests(MailerBase):
    def test_nothing_queued_when_disabled(self):
        """По умолчанию модуль выключен — письма не копятся."""
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        self.assertEqual(OutgoingEmail.objects.count(), 0)

    @override_settings(**MAIL_ON)
    def test_action_required_is_urgent(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        item = OutgoingEmail.objects.get()
        self.assertEqual(item.kind, OutgoingEmail.Kind.URGENT)
        self.assertEqual(item.status, OutgoingEmail.Status.QUEUED)
        self.assertEqual(item.link_path, "/approvals")

    @override_settings(**MAIL_ON)
    def test_decision_goes_to_digest(self):
        self.notify(Notification.NotificationType.APPROVAL_DECIDED)
        self.assertEqual(OutgoingEmail.objects.get().kind,
                         OutgoingEmail.Kind.DIGEST)

    @override_settings(**MAIL_ON)
    def test_exception_request_is_urgent(self):
        self.notify(Notification.NotificationType.EXCEPTION_REQUIRED)
        self.assertEqual(OutgoingEmail.objects.get().kind,
                         OutgoingEmail.Kind.URGENT)

    @override_settings(**MAIL_ON)
    def test_opted_out_employee_marked_skipped(self):
        """Отказ фиксируется явно, а не молчаливым отсутствием письма."""
        self.employee.email_notifications_enabled = False
        self.employee.save()
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        item = OutgoingEmail.objects.get()
        self.assertEqual(item.status, OutgoingEmail.Status.SKIPPED)

        send_urgent()
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**MAIL_ON)
    def test_address_is_frozen_at_queue_time(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        self.employee.email = "moved@corp.ru"
        self.employee.save()
        self.assertEqual(OutgoingEmail.objects.get().recipient_email, "user@corp.ru")


@override_settings(**MAIL_ON)
class SendingTests(MailerBase):
    def test_urgent_sent_immediately(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED,
                    "Иванов отправил график на 2027 год")
        result = send_urgent()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["user@corp.ru"])
        self.assertIn("согласование", message.subject.lower())
        self.assertIn("Иванов", message.body)
        self.assertIn("https://vacation.corp.ru/approvals", message.body)

    def test_html_alternative_attached(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        send_urgent()
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("<a href=", html)
        self.assertIn("vacation.corp.ru", html)

    def test_brand_colour_used_in_html(self):
        settings_row = CompanySettings.get_solo()
        settings_row.brand_primary_color = "#AA3366"
        settings_row.organization_name = "Моя компания"
        settings_row.save()

        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        send_urgent()
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("#AA3366", html)
        self.assertIn("Моя компания", html)

    def test_sent_only_once(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        send_urgent()
        send_urgent()
        self.assertEqual(len(mail.outbox), 1)

    def test_digest_groups_by_recipient(self):
        for index in range(3):
            self.notify(Notification.NotificationType.APPROVAL_DECIDED,
                        f"Решение {index}")
        colleague = Employee.objects.create_user(
            email="other@corp.ru", employee_number="M2", full_name="Коллега",
            hire_date=date(2020, 1, 1))
        self.notify(Notification.NotificationType.APPROVAL_DECIDED,
                    "Чужое решение", recipient=colleague)

        result = send_digest()
        self.assertEqual(result["recipients"], 2)
        self.assertEqual(result["messages"], 4)
        self.assertEqual(len(mail.outbox), 2)

        mine = next(m for m in mail.outbox if m.to == ["user@corp.ru"])
        self.assertIn("3 события", mine.subject)
        for index in range(3):
            self.assertIn(f"Решение {index}", mine.body)

    def test_digest_does_not_touch_urgent(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        send_digest()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            OutgoingEmail.objects.get().status, OutgoingEmail.Status.QUEUED)

    def test_failure_keeps_message_in_queue(self):
        """Недоступный SMTP не должен терять уведомление."""
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        with patch("mailer.sending._deliver",
                   side_effect=OSError("SMTP недоступен")):
            result = send_urgent()

        self.assertEqual(result["failed"], 1)
        item = OutgoingEmail.objects.get()
        self.assertEqual(item.status, OutgoingEmail.Status.QUEUED)
        self.assertEqual(item.attempts, 1)
        self.assertTrue(item.last_error)

    def test_gives_up_after_max_attempts(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        item = OutgoingEmail.objects.get()
        item.attempts = 4
        item.save()
        with patch("mailer.sending._deliver",
                   side_effect=OSError("SMTP недоступен")):
            send_urgent()
        item.refresh_from_db()
        self.assertEqual(item.status, OutgoingEmail.Status.FAILED)

    def test_digest_failure_keeps_messages(self):
        self.notify(Notification.NotificationType.APPROVAL_DECIDED)
        with patch("mailer.sending._deliver",
                   side_effect=OSError("SMTP недоступен")):
            result = send_digest()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(
            OutgoingEmail.objects.get().status, OutgoingEmail.Status.QUEUED)

    def test_failed_message_not_retried(self):
        self.notify(Notification.NotificationType.APPROVAL_REQUIRED)
        OutgoingEmail.objects.update(status=OutgoingEmail.Status.FAILED)
        self.assertEqual(send_urgent()["sent"], 0)


class ProfileSettingTests(MailerBase):
    def test_flag_visible_in_profile(self):
        self.client.force_authenticate(self.employee)
        body = self.client.get("/api/v1/auth/me/").json()
        self.assertTrue(body["email_notifications_enabled"])

    def test_employee_can_opt_out(self):
        self.client.force_authenticate(self.employee)
        response = self.client.patch("/api/v1/auth/me/", {
            "email_notifications_enabled": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["email_notifications_enabled"])
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.email_notifications_enabled)

    def test_other_fields_ignored(self):
        """Через настройки профиля нельзя выдать себе права администратора."""
        self.client.force_authenticate(self.employee)
        self.client.patch("/api/v1/auth/me/", {
            "is_admin": True, "full_name": "Взломщик"}, format="json")
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.is_admin)
        self.assertEqual(self.employee.full_name, "Пользователь")


@override_settings(**MAIL_ON)
class IntegrationTests(MailerBase):
    def test_approval_flow_produces_letters(self):
        """Письма появляются из реальных событий, а не только из тестов."""
        from schedule import approvals
        from schedule.models import ScheduleEntry
        from schedule.tests import seed_rules

        seed_rules()
        chief = Employee.objects.create_user(
            email="chief@corp.ru", employee_number="M9", full_name="Начальник",
            hire_date=date(2015, 1, 1), org_unit=self.employee.org_unit)
        self.employee.org_unit.manager = chief
        self.employee.org_unit.save()

        for start, end in [(date(2027, 7, 5), date(2027, 7, 18)),
                           (date(2027, 9, 6), date(2027, 9, 19))]:
            ScheduleEntry.objects.create(
                employee=self.employee, created_by=self.employee,
                record_type=ScheduleEntry.RecordType.VACATION,
                date_from=start, date_to=end)

        approval = approvals.submit_year(self.employee, 2027, self.employee)
        urgent = OutgoingEmail.objects.filter(kind=OutgoingEmail.Kind.URGENT)
        self.assertEqual(urgent.count(), 1)
        self.assertEqual(urgent.first().recipient_email, "chief@corp.ru")

        approvals.decide_step(approval.steps.first(), chief, approved=True)
        digest = OutgoingEmail.objects.filter(kind=OutgoingEmail.Kind.DIGEST)
        self.assertEqual(digest.count(), 1)
        self.assertEqual(digest.first().recipient_email, "user@corp.ru")
