"""Отправка очереди писем.

Ставится в планировщик:
  * срочные — каждые несколько минут:   send_mail_queue
  * сводка — раз в день утром:          send_mail_queue --digest
"""

from django.core.management.base import BaseCommand

from config.commands import WorkingDatabaseChecks
from mailer.composing import mail_enabled
from mailer.sending import send_digest, send_urgent


class Command(WorkingDatabaseChecks, BaseCommand):
    help = "Отправляет письма из очереди"

    def add_arguments(self, parser):
        parser.add_argument("--digest", action="store_true",
                            help="Отправить дневную сводку вместо срочных писем")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        if not mail_enabled():
            self.stdout.write(self.style.WARNING(
                "Почтовые уведомления выключены (MAIL_NOTIFICATIONS_ENABLED)"))
            return

        if options["digest"]:
            result = send_digest()
            self.stdout.write(self.style.SUCCESS(
                f"Сводка: получателей {result['recipients']}, "
                f"событий {result['messages']}, ошибок {result['failed']}"))
            return

        result = send_urgent(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(
            f"Срочные: отправлено {result['sent']}, ошибок {result['failed']}"))
