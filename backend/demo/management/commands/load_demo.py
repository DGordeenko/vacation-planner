"""Загрузка демонстрационных данных.

Очищает демо-базу и наполняет её заново, поэтому годится и для сброса
демо после того, как посетители его поменяли. Рабочую базу не трогает:
работает только с базой ``demo`` и отказывается, если она совпадает
с рабочей.
"""

import time

from django.core.management.base import BaseCommand, CommandError

from demo.loader import DemoLoadError, load


class Command(BaseCommand):
    help = "Очищает демо-базу и загружает демонстрационные данные"

    def handle(self, *args, **options):
        started = time.monotonic()
        try:
            report = load(stdout=self.stdout)
        except DemoLoadError as error:
            raise CommandError(str(error)) from error
        for number, start, days in report.skipped_periods:
            self.stderr.write(
                f"  Не внесён отпуск {number} с {start} на {days} дн.: не прошёл проверки")
        self.stdout.write(f"Готово за {time.monotonic() - started:.1f} с.")
