#!/bin/sh
# Резервное копирование базы графика отпусков: pg_dump в сжатом формате
# custom (-Fc) раз в BACKUP_INTERVAL_HOURS часов, копии старше
# BACKUP_KEEP_DAYS дней удаляются. Восстановление — docs/16-backups.md.
#
# Подключение — стандартными переменными libpq: PGHOST, PGPORT, PGUSER,
# PGPASSWORD, PGDATABASE. Пароль не попадает ни в командную строку, ни в лог.
#
# BACKUP_ONCE=1 — одна копия и выход (cron, CronJob Kubernetes, ручной запуск).
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
BACKUP_INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
BACKUP_ONCE="${BACKUP_ONCE:-0}"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') backup: $*"; }

backup_once() {
    stamp="$(date -u '+%Y%m%d-%H%M%S')"
    target="${BACKUP_DIR}/${PGDATABASE:-vacation}-${stamp}.dump"
    partial="${target}.partial"
    # Пишем во временный файл и переименовываем только после успеха:
    # оборванная копия не должна выглядеть годной для восстановления.
    if pg_dump --format=custom --no-owner --file="${partial}"; then
        mv "${partial}" "${target}"
        log "готово ${target} ($(du -h "${target}" | cut -f1))"
    else
        rm -f "${partial}"
        log "ОШИБКА: копия не создана"
        return 1
    fi
    # Удаляем только свои файлы и только после успешной новой копии:
    # при сломанном pg_dump старые копии остаются.
    find "${BACKUP_DIR}" -maxdepth 1 -name '*.dump' -type f \
        -mtime "+$((BACKUP_KEEP_DAYS - 1))" -print -delete | while read -r old; do
        log "удалена старая копия ${old}"
    done
}

mkdir -p "${BACKUP_DIR}"

if [ "${BACKUP_ONCE}" = "1" ]; then
    backup_once
    exit $?
fi

log "старт: каждые ${BACKUP_INTERVAL_HOURS} ч, хранение ${BACKUP_KEEP_DAYS} дн., каталог ${BACKUP_DIR}"
while true; do
    # Сбой одной копии не останавливает расписание: следующая попытка — по плану.
    backup_once || true
    sleep "$((BACKUP_INTERVAL_HOURS * 3600))"
done
