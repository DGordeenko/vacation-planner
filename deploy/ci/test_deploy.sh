#!/usr/bin/env bash
# Проверка логики deploy.sh без кластера: kubectl и curl подменяются
# заглушками, которые пишут вызовы в журнал и имитируют сбои.
#   deploy/ci/test_deploy.sh   (нужен render.py-результат: создаётся здесь)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"

IMAGE_TAG=sha-test BACKEND_IMAGE=r.example.com/b FRONTEND_IMAGE=r.example.com/f \
  APP_DOMAIN=app.example.com python3 "$HERE/render.py" "$T/out" >/dev/null

cat > "$T/bin/kubectl" <<'K'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
case "$*" in
  *"get deployment/backend -o jsonpath"*) echo "r.example.com/b:sha-old" ;;
  *"get deployment/frontend -o jsonpath"*) echo "r.example.com/f:sha-old" ;;
  *"--for=condition=complete"*) exit 0 ;;
  *"--for=condition=failed"*) sleep 5 ;;
  *"jsonpath={.status.succeeded}"*) [ "${FAIL_JOB:-}" = 1 ] && echo 0 || echo 1 ;;
  *"rollout status deployment/frontend"*) [ "${FAIL_FRONTEND:-}" = 1 ] && exit 1 || exit 0 ;;
esac
exit 0
K
cat > "$T/bin/curl" <<'C'
#!/usr/bin/env bash
case "$*" in
  *"%{http_code}"*) [ "${FAIL_SMOKE:-}" = 1 ] && echo 200 || echo 403 ;;
  *"/api/health/ready/"*) echo '{"status":"ok"}' ;;
  *) echo '<div id="root">' ;;
esac
C
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" APP_URL=https://app.example.com

run() { export LOG="$T/log.$1"; : > "$LOG"; shift; env "$@" "$HERE/deploy.sh" "$T/out" >"$T/out.txt" 2>&1; }
fail() { echo "FAIL: $*"; cat "$T/out.txt"; exit 1; }

# 1. Успех: копия → миграции → бэкенд → фронтенд, без отката.
run ok || fail "успешная выкатка завершилась ошибкой"
order=$(grep -nE "apply -f .*/(backup|migrate|app|frontend)\.yaml" "$T/log.ok" | sed 's/.*\///')
[ "$(echo "$order" | tr '\n' ' ')" = "backup.yaml migrate.yaml app.yaml frontend.yaml " ] || fail "порядок: $order"
grep -q "set image" "$T/log.ok" && fail "откат при успехе"

# 2. Сбой миграций: поды не трогаются.
run mig FAIL_JOB=1 && fail "сбой миграций не остановил выкатку"
grep -q "app.yaml" "$T/log.mig" && fail "после сбоя миграций применён app.yaml"

# 3. Сбой фронтенда: возврат обоих на прежние образы.
run fe FAIL_FRONTEND=1 && fail "сбой фронтенда не вернул ошибку"
grep -q "set image deployment/backend backend=r.example.com/b:sha-old" "$T/log.fe" || fail "бэкенд не откачен"
grep -q "set image deployment/frontend frontend=r.example.com/f:sha-old" "$T/log.fe" || fail "фронтенд не откачен"

# 4. Без копии.
run nob BACKUP_ENABLED=0 || fail "выкатка без копии"
grep -q "backup.yaml" "$T/log.nob" && fail "копия при BACKUP_ENABLED=0"
# 5. Smoke не прошёл (админка открыта всем): откат.
run smoke FAIL_SMOKE=1 && fail "провал smoke не вернул ошибку"
grep -q "set image deployment/backend" "$T/log.smoke" || fail "нет отката после smoke"
echo "deploy.sh: все сценарии пройдены"
