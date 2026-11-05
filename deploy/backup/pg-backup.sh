#!/bin/sh
# Резервное копирование базы графика отпусков: pg_dump в сжатом формате
# custom (-Fc), при заданном ключе — шифрование age, при заданном бакете —
# выгрузка в S3-совместимое хранилище. Восстановление — docs/16-backups.md.
#
# Подключение: DATABASE_URL (как у приложения) или стандартные переменные
# libpq: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE. TLS к базе —
# DB_SSLMODE/DB_SSLROOTCERT, как у приложения. Пароль не попадает ни
# в командную строку, ни в журнал.
#
# Переменные:
#   BACKUP_ONCE=1            одна копия и выход (CronJob, ручной запуск)
#   BACKUP_DIR               каталог копий, по умолчанию /backups
#   BACKUP_KEEP_DAYS         хранение в каталоге, по умолчанию 14 дней
#   BACKUP_INTERVAL_HOURS    период в режиме цикла, по умолчанию 24
#   BACKUP_NAME              префикс имени файла, по умолчанию имя базы
#   BACKUP_AGE_RECIPIENTS    открытые ключи age через запятую — шифровать
#   BACKUP_REQUIRE_ENCRYPTION=1  без ключа — ошибка, а не открытая копия
#   BACKUP_S3_BUCKET         бакет; задан — копия выгружается и локально
#                            не хранится (срок — правило жизненного цикла бакета)
#   BACKUP_S3_PREFIX         каталог в бакете, по умолчанию daily
#   S3_ENDPOINT              адрес S3 API облака
#   S3_SSE, S3_SSE_KMS_KEY_ID  серверное шифрование (aws:kms и ключ KMS)
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION — доступ к S3
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
BACKUP_INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
BACKUP_ONCE="${BACKUP_ONCE:-0}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-daily}"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') backup: $*"; }

[ -n "${DB_SSLMODE:-}" ] && export PGSSLMODE="$DB_SSLMODE"
[ -n "${DB_SSLROOTCERT:-}" ] && export PGSSLROOTCERT="$DB_SSLROOTCERT"

# Имя базы для имени файла: из DATABASE_URL, PGDATABASE или «vacation».
if [ -n "${DATABASE_URL:-}" ]; then
    db_from_url=$(printf '%s' "$DATABASE_URL" | sed -E 's#^[^/]*//[^/]*/([^?]*).*#\1#')
fi
NAME="${BACKUP_NAME:-${db_from_url:-${PGDATABASE:-vacation}}}"

if [ -n "${BACKUP_AGE_RECIPIENTS:-}" ]; then
    SUFFIX=".dump.age"
    set --
    for key in $(echo "$BACKUP_AGE_RECIPIENTS" | tr ',' ' '); do set -- "$@" -r "$key"; done
    AGE_ARGS="$*"
elif [ "${BACKUP_REQUIRE_ENCRYPTION:-0}" = "1" ]; then
    log "ОШИБКА: BACKUP_REQUIRE_ENCRYPTION=1, но не задан BACKUP_AGE_RECIPIENTS"
    exit 1
else
    SUFFIX=".dump"
    AGE_ARGS=""
fi

dump() {
    if [ -n "${DATABASE_URL:-}" ]; then
        pg_dump --format=custom --no-owner --dbname="$DATABASE_URL"
    else
        pg_dump --format=custom --no-owner
    fi
}

backup_once() {
    stamp="$(date -u '+%Y%m%d-%H%M%S')"
    file="${NAME}-${stamp}${SUFFIX}"
    target="${BACKUP_DIR}/${file}"
    partial="${target}.partial"
    # Пишем во временный файл и переименовываем только после успеха:
    # оборванная копия не должна выглядеть годной для восстановления.
    # Шифрование — потоком: открытый дамп не касается диска.
    # Успех pg_dump внутри конвейера проверяется через файл статуса:
    # pipefail есть не во всех оболочках, а без него ошибка pg_dump
    # потерялась бы за успешным age.
    status="${partial}.status"
    rm -f "$status"
    if [ -n "$AGE_ARGS" ]; then
        # shellcheck disable=SC2086  # AGE_ARGS — список «-r ключ», делится намеренно
        { dump || echo failed > "$status"; } | age $AGE_ARGS > "$partial" || echo failed > "$status"
    else
        dump > "$partial" || echo failed > "$status"
    fi
    if [ ! -e "$status" ] && [ -s "$partial" ]; then
        mv "$partial" "$target"
        log "готово ${target} ($(du -h "$target" | cut -f1))"
    else
        rm -f "$partial" "$status"
        log "ОШИБКА: копия не создана"
        return 1
    fi

    if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
        set -- s3 cp "$target" "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/${file}" --only-show-errors
        [ -n "${S3_ENDPOINT:-}" ] && set -- "$@" --endpoint-url "$S3_ENDPOINT"
        [ -n "${S3_SSE:-}" ] && set -- "$@" --sse "$S3_SSE"
        [ -n "${S3_SSE_KMS_KEY_ID:-}" ] && set -- "$@" --sse-kms-key-id "$S3_SSE_KMS_KEY_ID"
        if aws "$@"; then
            log "выгружено s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/${file}"
            # В бакете срок хранения задаёт правило жизненного цикла;
            # локальная копия была только промежуточной.
            rm -f "$target"
        else
            log "ОШИБКА: выгрузка не удалась, копия оставлена в ${target}"
            return 1
        fi
        return 0
    fi

    # Удаляем только копии этой базы (префикс имени) и только после
    # успешной новой копии: соседние файлы в каталоге не трогаем, а при
    # сломанном pg_dump старые копии остаются.
    find "$BACKUP_DIR" -maxdepth 1 -type f \( -name "${NAME}-*.dump" -o -name "${NAME}-*.dump.age" \) \
        -mtime "+$((BACKUP_KEEP_DAYS - 1))" -print -delete | while read -r old; do
        log "удалена старая копия ${old}"
    done
}

mkdir -p "$BACKUP_DIR"

if [ "$BACKUP_ONCE" = "1" ]; then
    backup_once
    exit $?
fi

log "старт: каждые ${BACKUP_INTERVAL_HOURS} ч, хранение ${BACKUP_KEEP_DAYS} дн., каталог ${BACKUP_DIR}"
while true; do
    # Сбой одной копии не останавливает расписание: следующая попытка — по плану.
    backup_once || true
    sleep "$((BACKUP_INTERVAL_HOURS * 3600))"
done
