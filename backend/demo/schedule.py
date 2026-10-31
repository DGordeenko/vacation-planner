"""Ежедневный сброс демо-данных.

Посетители демо меняют общие данные: отправляют графики, отклоняют,
вносят отгулы. Чтобы каждый новый посетитель видел все сценарии с начала,
демо загружается заново раз в сутки (по умолчанию в полночь по часовому
поясу проекта, TIME_ZONE).
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def parse_reset_time(raw: str) -> time:
    """«00:00» → time(0, 0). Неверное значение — ошибка запуска, а не тихий полдень."""
    try:
        hours, minutes = (int(part) for part in raw.strip().split(":"))
        return time(hours, minutes)
    except (ValueError, TypeError) as error:
        raise ValueError(f"DEMO_RESET_TIME: ожидается ЧЧ:ММ, получено «{raw}»") from error


def next_run(now: datetime, at: time, tz: ZoneInfo) -> datetime:
    """Ближайший момент `at` по местному времени строго позже `now`."""
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), at, tzinfo=tz)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), at, tzinfo=tz)
    return candidate
