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

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile -
