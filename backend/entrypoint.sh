#!/bin/sh
set -e

# Команда вместо веб-сервера: `docker compose run backend python manage.py
# send_mail_queue`, start command cron-сервиса Railway, CronJob Kubernetes.
# Миграции и подготовка демо для разовой команды не нужны.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Без собственного SECRET_KEY сервис не стартует: со значением по умолчанию
# из репозитория подписи сессий и ссылок предсказуемы для любого.
# Образец из .env.example — тоже не ключ: его видят все.
# SECRET_KEY_FILE — ключ в файле (смонтированный секрет): проверяется он.
KEY="${SECRET_KEY:-}"
if [ -n "${SECRET_KEY_FILE:-}" ]; then
    KEY=$(cat "$SECRET_KEY_FILE") || { echo "ОШИБКА: не читается SECRET_KEY_FILE=$SECRET_KEY_FILE" >&2; exit 1; }
fi
case "$KEY" in
    ""|dev-only-insecure-key-replace-in-production|замените-на-случайное-значение) KEY_IS_PUBLIC=1 ;;
    *) KEY_IS_PUBLIC=0 ;;
esac
if [ "$KEY_IS_PUBLIC" = "1" ]; then
    case "${ALLOW_INSECURE_SECRET_KEY:-0}" in 1|true|True|yes) ;; *)
        echo "ОШИБКА: не задан SECRET_KEY. Задайте случайную строку от 50 символов" \
             "(ALLOW_INSECURE_SECRET_KEY=1 — только для локальной проверки)." >&2
        exit 1 ;;
    esac
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
# если пользователь уже есть, ничего не меняет. Выполняется там же, где
# миграции: при RUN_MIGRATIONS=0 (Kubernetes) её делает Job миграций, и
# веб-подам пароль администратора не нужен вовсе — его не будет в их
# окружении. Без переменных команда просто пропускается.
# Пароль не выводится: команда его не печатает, а set -x здесь не включён.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py ensure_admin
fi

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

# Журнал запросов без строки запроса (%(U)s — только путь) и без Referer:
# в них бывают код входа Keycloak (?code=…) и токен приглашения
# (?invite=… — страница, с которой пришёл запрос). Адрес клиента — из
# X-Real-IP, который ставит nginx; %(h)s был бы адресом самого nginx.
ACCESS_LOG_FORMAT='%({x-real-ip}i)s "%(m)s %(U)s" %(s)s %(B)s %(M)sms'
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --access-logformat "$ACCESS_LOG_FORMAT" \
    --error-logfile -
