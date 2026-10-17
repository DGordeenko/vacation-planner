"""Подписка на события графика.

Обработчик объявлен на уровне модуля намеренно: Django хранит получателей
сигналов по слабой ссылке, и вложенная функция может быть собрана сборщиком
мусора — тогда письма молча перестают ставиться в очередь. Дополнительно
подключение идёт с weak=False.
"""

from django.db.models.signals import post_save

from mailer.composing import queue_for_notification


def handle_notification_created(sender, instance, created, **kwargs):
    if created:
        queue_for_notification(instance)


def connect():
    from schedule.models import Notification

    post_save.connect(
        handle_notification_created,
        sender=Notification,
        dispatch_uid="mailer_queue_for_notification",
        weak=False,
    )
