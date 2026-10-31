"""Фоновый процесс: каждый день в DEMO_RESET_TIME загружает демо заново.

Запускается из entrypoint.sh рядом с gunicorn, когда демо включено.
Сбой одной загрузки не останавливает расписание: в лог пишется
предупреждение, следующая попытка — через сутки. Рабочую базу не трогает:
загрузка работает только с базой demo (как load_demo).
"""

import os
import time as clock
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from demo.loader import load
from demo.schedule import next_run, parse_reset_time


class Command(BaseCommand):
    help = "Ежедневно перезагружает демо-данные (по умолчанию в полночь)"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true",
                            help="Дождаться ближайшего срока, загрузить и выйти")

    def handle(self, *args, **options):
        if not settings.DEMO_MODE_ENABLED:
            raise CommandError("Демо-режим выключен. Включается явно: DEMO_MODE_ENABLED=True.")
        try:
            at = parse_reset_time(os.environ.get("DEMO_RESET_TIME", "00:00"))
        except ValueError as error:
            raise CommandError(str(error)) from error
        tz = ZoneInfo(settings.TIME_ZONE)

        while True:
            moment = next_run(datetime.now(tz), at, tz)
            self.stdout.write(f"Демо: следующий сброс {moment:%d.%m.%Y %H:%M} ({tz.key})")
            self.stdout.flush()
            # Спим кусками: при переводе часов или «уснувшем» контейнере
            # срок пересчитывается, а не проскакивает на сутки.
            while (wait := (moment - datetime.now(tz)).total_seconds()) > 0:
                clock.sleep(min(wait, 300))
            try:
                load()
                self.stdout.write("Демо: данные загружены заново.")
            except Exception as error:  # расписание не должно падать из-за одной загрузки
                self.stderr.write(f"ВНИМАНИЕ: сброс демо не удался: {error}")
            if options["once"]:
                return
