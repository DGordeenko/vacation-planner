"""Расписание почтовой очереди внутри контейнера бэкенда.

Срочные письма — каждые MAIL_SEND_INTERVAL_MINUTES минут (по умолчанию 5),
дневная сводка — по будням в MAIL_DIGEST_TIME (по умолчанию 09:00 по часовому
поясу проекта). Так почта работает на одной реплике без внешнего cron —
в Docker Compose и на Railway. При нескольких репликах расписание
выключается (MAIL_SCHEDULER=0) и переносится в CronJob Kubernetes.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_digest(now: datetime, at: time, tz: ZoneInfo) -> datetime:
    """Ближайший будний день в `at`, строго позже `now`."""
    local = now.astimezone(tz)
    day = local.date()
    while True:
        candidate = datetime.combine(day, at, tzinfo=tz)
        if candidate > local and candidate.weekday() < 5:
            return candidate
        day += timedelta(days=1)
