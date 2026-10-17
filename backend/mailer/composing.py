"""Постановка писем в очередь по событиям системы."""

from django.conf import settings

from schedule.models import Notification

# Что считается срочным: события, требующие действия получателя.
# Решения по заявкам уходят сводкой — они информируют, а не требуют.
URGENT_TYPES = {
    Notification.NotificationType.APPROVAL_REQUIRED,
    Notification.NotificationType.EXCEPTION_REQUIRED,
    Notification.NotificationType.TAG_LIMIT_ALERT,
}

DIGEST_TYPES = {
    Notification.NotificationType.APPROVAL_DECIDED,
}

LINKS = {
    Notification.NotificationType.APPROVAL_REQUIRED: "/approvals",
    Notification.NotificationType.EXCEPTION_REQUIRED: "/approvals",
    Notification.NotificationType.TAG_LIMIT_ALERT: "/team",
    Notification.NotificationType.APPROVAL_DECIDED: "/",
}

SUBJECTS = {
    Notification.NotificationType.APPROVAL_REQUIRED: "Требуется согласование графика",
    Notification.NotificationType.EXCEPTION_REQUIRED: "Требуется подтверждение исключения",
    Notification.NotificationType.TAG_LIMIT_ALERT: "Превышен лимит одновременного отсутствия",
    Notification.NotificationType.APPROVAL_DECIDED: "Решение по вашей заявке",
}


def mail_enabled() -> bool:
    return getattr(settings, "MAIL_NOTIFICATIONS_ENABLED", False)


def queue_for_notification(notification):
    """Создаёт письмо для уведомления, если оно подлежит отправке."""
    from mailer.models import OutgoingEmail

    if not mail_enabled():
        return None

    kind = None
    if notification.notification_type in URGENT_TYPES:
        kind = OutgoingEmail.Kind.URGENT
    elif notification.notification_type in DIGEST_TYPES:
        kind = OutgoingEmail.Kind.DIGEST
    if kind is None:
        return None

    recipient = notification.recipient
    if not recipient.email:
        return None
    # Отказ сотрудника фиксируется как пропуск, а не молчаливое отсутствие
    # письма: иначе потом не выяснить, почему уведомление не пришло.
    status = (OutgoingEmail.Status.QUEUED
              if recipient.email_notifications_enabled
              else OutgoingEmail.Status.SKIPPED)

    return OutgoingEmail.objects.create(
        recipient=recipient,
        recipient_email=recipient.email,
        kind=kind,
        subject=SUBJECTS.get(notification.notification_type, "Уведомление"),
        body=notification.message,
        link_path=LINKS.get(notification.notification_type, "/"),
        notification=notification,
        status=status,
    )
