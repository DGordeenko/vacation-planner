import uuid

from django.db import models


class AuditLog(models.Model):
    """Журнал изменений.

    Пишется автоматически сигналами: явные вызовы по коду означали бы,
    что каждое новое место изменения данных нужно не забыть обвязать,
    а обнаружился бы пропуск только когда история понадобится.
    """

    class Action(models.TextChoices):
        CREATE = "create", "Создание"
        UPDATE = "update", "Изменение"
        DELETE = "delete", "Удаление"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity_type = models.CharField(max_length=64, db_index=True)
    entity_id = models.UUIDField(null=True, blank=True, db_index=True)
    entity_label = models.CharField(
        max_length=255, blank=True,
        help_text="Человекочитаемое описание объекта на момент записи")
    action = models.CharField(max_length=16, choices=Action.choices)

    changed_by = models.ForeignKey(
        "employees.Employee", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_records")
    changed_by_name = models.CharField(
        max_length=255, blank=True,
        help_text="Имя на момент записи — сохраняется, даже если учётку удалят")

    # Чей объект: позволяет руководителю смотреть историю по своим людям
    subject_employee = models.ForeignKey(
        "employees.Employee", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_subject_records")

    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    diff = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=32, blank=True,
                              help_text="api, import, admin, system")

    class Meta:
        ordering = ["-changed_at"]
        verbose_name = "Запись аудита"
        verbose_name_plural = "Журнал изменений"
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self):
        return f"{self.get_action_display()} {self.entity_type} — {self.changed_by_name}"
