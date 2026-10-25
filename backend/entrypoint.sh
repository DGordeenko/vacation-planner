#!/bin/sh
set -e

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

# Демо-режим (docs/15-demo-mode.md): отдельная база, данные загружаются
# заново при каждом старте — так демо само сбрасывается после посетителей.
# Миграции демо-базы идут здесь же, а не по RUN_MIGRATIONS: по умолчанию
# демо-база — SQLite внутри контейнера, и у каждой реплики она своя.
# DEMO_LOAD_ON_START=0 — для общей демо-базы Postgres, которую готовит Job.
is_true() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes) return 0 ;;
        *) return 1 ;;
    esac
}
if is_true "${DEMO_MODE_ENABLED:-0}" && is_true "${DEMO_LOAD_ON_START:-1}"; then
    echo "Готовим демо-базу..."
    python manage.py migrate --database=demo --noinput
    python manage.py load_demo
fi

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile -
