#!/bin/sh
set -e

# Команда вместо веб-сервера: `docker compose run backend python manage.py
# send_mail_queue`, start command cron-сервиса Railway, CronJob Kubernetes.
# Миграции и подготовка демо для разовой команды не нужны.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Миграции при старте удобны для Docker и Railway, где контейнер один.
# В Kubernetes несколько реплик стартуют одновременно и конкурируют —
# там миграции выносятся в Job или initContainer, а здесь отключаются
# переменной RUN_MIGRATIONS=0 (см. docs/tasks/00-skeleton.md, итерация 3).
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "Применяем миграции..."
    python manage.py migrate --noinput
fi

# Первый администратор из ADMIN_EMAIL и ADMIN_PASSWORD. Команда идемпотентна:
# если пользователь уже есть, ничего не меняет, поэтому безопасна при каждом
# старте. Вызывается и при RUN_MIGRATIONS=0 — в Kubernetes миграции идут
# отдельным Job до старта реплик. Без переменных команда просто пропускается.
# Пароль не выводится: команда его не печатает, а set -x здесь не включён.
python manage.py ensure_admin

# Демо-режим (docs/15-demo-mode.md): только при явном DEMO_MODE_ENABLED=True.
# Отдельная база, данные загружаются заново при каждом старте — так демо
# само сбрасывается после посетителей. В рабочую базу не пишет никогда.
# Миграции демо-базы идут здесь же, а не по RUN_MIGRATIONS: по умолчанию
# демо-база — SQLite во временном каталоге, у каждой реплики своя.
# DEMO_LOAD_ON_START=0 — для общей демо-базы Postgres, которую готовит Job.
#
# Сбой демо не останавливает запуск: демо — витрина, а не часть рабочей
# системы. Без загруженных данных кнопка демо на экране входа не появится.
is_true() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes) return 0 ;;
        *) return 1 ;;
    esac
}
if is_true "${DEMO_MODE_ENABLED:-0}" && is_true "${DEMO_LOAD_ON_START:-1}"; then
    echo "Готовим демо-базу..."
    if python manage.py migrate --database=demo --noinput && python manage.py load_demo; then
        echo "Демо-режим готов."
    else
        echo "ВНИМАНИЕ: демо-база не подготовлена, демо-режим недоступен." \
             "Рабочая система запускается. Причина — в сообщениях выше." >&2
    fi
fi

# Ежедневный сброс демо (по умолчанию в полночь, DEMO_RESET_TIME=ЧЧ:ММ):
# фоновый процесс рядом с gunicorn. DEMO_RESET_DAILY=0 — выключить, например
# при общей демо-базе на несколько реплик: там сброс — отдельный CronJob.
if is_true "${DEMO_MODE_ENABLED:-0}" && is_true "${DEMO_RESET_DAILY:-1}"; then
    python manage.py demo_reset_daily &
fi

# Почтовая очередь по расписанию (docs/12-mail-setup.md): фоновый процесс
# рядом с gunicorn, когда почта включена. MAIL_SCHEDULER=0 — при нескольких
# репликах, там отправку делают CronJob.
if is_true "${MAIL_NOTIFICATIONS_ENABLED:-0}" && is_true "${MAIL_SCHEDULER:-1}"; then
    python manage.py mail_scheduler &
fi

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile -
