"""Фоновый процесс отправки почты: срочные — часто, сводка — по будням утром.

Запускается из entrypoint.sh рядом с gunicorn при MAIL_NOTIFICATIONS_ENABLED.
Каждая отправка — та же команда send_mail_queue, что и для внешнего cron.
Сбой одной отправки расписание не останавливает: письма остаются в очереди.
"""

import os
import time as clock
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from config.commands import WorkingDatabaseChecks
from demo.schedule import parse_reset_time
from mailer.schedule import next_digest


class Command(WorkingDatabaseChecks, BaseCommand):
    help = "Отправляет почтовую очередь по расписанию (для одной реплики)"

    def handle(self, *args, **options):
        try:
            interval = max(1, int(os.environ.get("MAIL_SEND_INTERVAL_MINUTES") or "5")) * 60
            digest_at = parse_reset_time(os.environ.get("MAIL_DIGEST_TIME") or "09:00")
        except ValueError as error:
            raise CommandError(f"Расписание почты: {error}") from error
        tz = ZoneInfo(settings.TIME_ZONE)
        digest = next_digest(datetime.now(tz), digest_at, tz)
        self.stdout.write(f"Почта: срочные каждые {interval // 60} мин, "
                          f"сводка {digest:%d.%m.%Y %H:%M}")
        while True:
            self._run()
            if datetime.now(tz) >= digest:
                self._run(digest=True)
                digest = next_digest(datetime.now(tz), digest_at, tz)
            clock.sleep(interval)

    def _run(self, digest=False):
        try:
            call_command("send_mail_queue", digest=digest, stdout=self.stdout)
        except Exception as error:  # письма остаются в очереди до следующего раза
            self.stderr.write(f"ВНИМАНИЕ: отправка почты не удалась: {error}")
