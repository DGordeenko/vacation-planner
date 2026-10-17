import uuid

from django.db import models


class OutgoingEmail(models.Model):
    """Очередь исходящих писем.

    Письма кладутся в очередь, а не отправляются прямо в запросе:
    недоступный SMTP не должен ронять сохранение графика или
    согласование. Отправкой занимается отдельная команда.
    """

    class Kind(models.TextChoices):
        URGENT = "urgent", "Срочное"
        DIGEST = "digest", "В сводку"

    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Пропущено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="outgoing_emails")
    # Адрес фиксируется на момент постановки в очередь: сотрудника могут
    # перевести или уволить, а письмо уже адресовано.
    recipient_email = models.EmailField()

    kind = models.CharField(max_length=16, choices=Kind.choices)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    # Ссылка на экран, где нужно принять решение.
    link_path = models.CharField(max_length=255, blank=True)

    notification = models.ForeignKey(
        "schedule.Notification", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="emails")

    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "kind"])]
        verbose_name = "Исходящее письмо"
        verbose_name_plural = "Очередь писем"

    def __str__(self):
        return f"{self.get_kind_display()} → {self.recipient_email}: {self.subject}"
