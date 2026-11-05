#!/usr/bin/env bash
# Выкатка версии в Kubernetes по шагам (.github/workflows/deploy.yml).
# Одинакова для всех облаков: облачная часть (вход в реестр и кластер)
# выполняется до неё, сюда приходит готовый kubectl.
#
#   deploy/ci/deploy.sh <каталог с результатом render.py>
#
# Порядок и почему так:
#   1. Копия базы — до миграций: миграции не откатываются, копия — да.
#   2. Миграции отдельным Job — до обновления подов: новый код может
#      требовать новых столбцов; ошибка останавливает выкатку целиком.
#   3. Бэкенд, затем фронтенд — фронтенд новой версии может вызывать
#      новые методы API.
#   4. Smoke через публичный адрес — то, что видит пользователь.
#   5. Сбой после шага 3 — возврат образов, которые работали до выкатки.
#
# Переменные:
#   NAMESPACE        по умолчанию vacation
#   APP_URL          https://домен — для smoke (обязательна)
#   BACKUP_ENABLED   1 (по умолчанию) — копия перед миграциями; 0 — без неё
#   SMOKE_ADMIN_CODE ожидаемый код /admin/login/ с раннера (по умолчанию 403)
#   ADMIN_PATH       путь админки, как DJANGO_ADMIN_PATH (по умолчанию admin)
#   ROLLOUT_TIMEOUT  по умолчанию 300s
set -Eeuo pipefail  # -E: ловушка ERR срабатывает и внутри функций

DIR=${1:?каталог с манифестами}
NS=${NAMESPACE:-vacation}
APP_URL=${APP_URL:?не задан APP_URL}
TIMEOUT=${ROLLOUT_TIMEOUT:-300s}
k() { kubectl -n "$NS" "$@"; }
step() { echo "::group::$*"; }
done_step() { echo "::endgroup::"; }

job_name() { sed -n 's/^  name: //p' "$1" | head -1; }

wait_job() {
    # kubectl wait не умеет «complete или failed» одной командой —
    # ждём любое из двух и затем смотрим, что вышло.
    local job=$1
    k wait --for=condition=complete "job/$job" --timeout="$TIMEOUT" &
    local ok=$!
    k wait --for=condition=failed "job/$job" --timeout="$TIMEOUT" &
    local bad=$!
    wait -n "$ok" "$bad" || true
    kill "$ok" "$bad" 2>/dev/null || true
    k logs "job/$job" --all-containers --tail=100 || true
    [ "$(k get "job/$job" -o jsonpath='{.status.succeeded}')" = "1" ] \
        || { echo "::error::Job $job завершился ошибкой"; return 1; }
}

# --- Образы, которые работают сейчас: на них откатываемся ---------------
PREV_BACKEND=$(k get deployment/backend -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
PREV_FRONTEND=$(k get deployment/frontend -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
echo "Сейчас: backend=${PREV_BACKEND:-нет} frontend=${PREV_FRONTEND:-нет}"

rollback() {
    echo "::error::Выкатка не удалась — возврат на предыдущие образы"
    if [ -n "$PREV_BACKEND" ]; then
        k set image deployment/backend "backend=$PREV_BACKEND"
        k rollout status deployment/backend --timeout="$TIMEOUT" || true
    fi
    if [ -n "$PREV_FRONTEND" ]; then
        k set image deployment/frontend "frontend=$PREV_FRONTEND"
        k rollout status deployment/frontend --timeout="$TIMEOUT" || true
    fi
    echo "::warning::Миграции не откатываются. Если новая схема несовместима" \
         "со старым кодом — восстановление из копии (docs/16-backups.md)."
    exit 1
}

# --- 1. Копия базы ------------------------------------------------------
if [ "${BACKUP_ENABLED:-1}" = "1" ]; then
    step "Резервная копия базы"
    k apply -f "$DIR/backup.yaml"
    wait_job "$(job_name "$DIR/backup.yaml")"
    done_step
fi

# --- 2. Миграции --------------------------------------------------------
step "Миграции"
MIGRATE=$(job_name "$DIR/migrate.yaml")
# Повторный запуск того же тега (workflow_dispatch): Job неизменяем.
k delete job "$MIGRATE" --ignore-not-found
k apply -f "$DIR/migrate.yaml"
wait_job "$MIGRATE"
done_step

# --- 3. Бэкенд, затем фронтенд -----------------------------------------
trap rollback ERR
step "Бэкенд"
k apply -f "$DIR/app.yaml"
k rollout status deployment/backend --timeout="$TIMEOUT"
done_step
step "Фронтенд"
k apply -f "$DIR/frontend.yaml"
k rollout status deployment/frontend --timeout="$TIMEOUT"
done_step

# --- 4. Smoke -----------------------------------------------------------
step "Проверка $APP_URL"
curl -fsS --retry 5 --retry-delay 5 --retry-all-errors "$APP_URL/api/health/ready/"
echo
curl -fsS "$APP_URL/" | grep -q '<div id="root">'
code=$(curl -s -o /dev/null -w '%{http_code}' "$APP_URL/${ADMIN_PATH:-admin}/login/")
[ "$code" = "${SMOKE_ADMIN_CODE:-403}" ] \
    || { echo "::error::/${ADMIN_PATH:-admin}/login/ ответил $code, ожидался ${SMOKE_ADMIN_CODE:-403}"; false; }
done_step
trap - ERR
echo "Выкатка завершена: $(k get deployment/backend -o jsonpath='{.spec.template.spec.containers[0].image}')"
