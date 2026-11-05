# Образ резервного копирования: pg_dump, шифрование age, выгрузка в
# S3-совместимое хранилище (AWS S3, Huawei OBS, Yandex Object Storage).
# Один образ для docker compose, CronJob Kubernetes и копии перед
# миграциями в конвейере выкатки (docs/16-backups.md).
#
# Версия pg_dump должна быть не ниже версии сервера: при переходе базы
# на новую мажорную версию PostgreSQL меняется и этот образ.
FROM postgres:16-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab

# age — шифрование открытым ключом: закрытый ключ хранится вне кластера,
# и утечка бакета или тома с копиями не раскрывает данные.
# Пакеты ставятся при сборке, а не при запуске: запуск без сети и без root.
# gosu из базового образа нужен только для запуска сервера PostgreSQL,
# которого здесь нет; собран старым Go, и сканер находил в нём уязвимости.
RUN apk upgrade --no-cache \
 && apk add --no-cache age aws-cli \
 && rm -f /usr/local/bin/gosu

COPY pg-backup.sh /usr/local/bin/pg-backup.sh
RUN chmod 0755 /usr/local/bin/pg-backup.sh

# Пользователь postgres из базового образа (UID 70): копия не требует root.
USER 70
ENTRYPOINT ["/usr/local/bin/pg-backup.sh"]
