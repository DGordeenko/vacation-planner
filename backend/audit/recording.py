"""Регистрация изменений через сигналы моделей."""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from audit.context import get_actor, get_source
from audit.models import AuditLog

# Поля, которые никогда не попадают в журнал: хеши паролей,
# токены приглашений, служебные отметки времени.
SKIP_FIELDS = {
    "password", "last_login", "invitation_token",
    "created_at", "updated_at", "changed_at",
}

# Поля, значения которых заменяются отметкой о наличии:
# записывать в открытый журнал их содержимое незачем.
MASK_FIELDS: set = set()

TRACKED = {}


def track(model, label=None, subject=None):
    """Регистрирует модель в аудите.

    label — как назвать объект в журнале, subject — как найти сотрудника,
    которого изменение касается (для видимости по оргструктуре).
    """
    TRACKED[model] = {
        "label": label or (lambda obj: str(obj)),
        "subject": subject or (lambda obj: None),
    }
    return model


def _serialize(value):
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _snapshot(instance):
    data = {}
    for field in instance._meta.fields:
        if field.name in SKIP_FIELDS:
            continue
        value = getattr(instance, field.attname, None)
        data[field.name] = _serialize(value)
    return data


def _write(instance, action, diff):
    config = TRACKED.get(type(instance))
    if config is None:
        return
    actor = get_actor()
    try:
        subject = config["subject"](instance)
    except Exception:
        subject = None
    try:
        label = config["label"](instance)[:255]
    except Exception:
        label = ""

    AuditLog.objects.create(
        entity_type=type(instance).__name__,
        entity_id=getattr(instance, "id", None) if _is_uuid(instance) else None,
        entity_label=label,
        action=action,
        changed_by=actor,
        changed_by_name=getattr(actor, "full_name", "") or "Система",
        subject_employee=subject,
        diff=diff,
        source=get_source(),
    )


def _is_uuid(instance):
    from uuid import UUID
    return isinstance(getattr(instance, "id", None), UUID)


@receiver(pre_save)
def remember_previous(sender, instance, **kwargs):
    if sender not in TRACKED or instance.pk is None:
        return
    try:
        instance._audit_previous = _snapshot(sender.objects.get(pk=instance.pk))
    except sender.DoesNotExist:
        instance._audit_previous = None


@receiver(post_save)
def record_save(sender, instance, created, **kwargs):
    if sender not in TRACKED:
        return

    if created:
        _write(instance, AuditLog.Action.CREATE, {"after": _snapshot(instance)})
        return

    previous = getattr(instance, "_audit_previous", None)
    if previous is None:
        return

    current = _snapshot(instance)
    changes = {
        field: {"was": previous.get(field), "became": current.get(field)}
        for field in current
        if previous.get(field) != current.get(field)
    }
    # Сохранение без фактических изменений журнал не засоряет.
    if not changes:
        return
    _write(instance, AuditLog.Action.UPDATE, changes)


@receiver(post_delete)
def record_delete(sender, instance, **kwargs):
    if sender not in TRACKED:
        return
    _write(instance, AuditLog.Action.DELETE, {"before": _snapshot(instance)})
