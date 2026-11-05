#!/bin/sh
# Список адресов, которым открыта админка Django через этот nginx.
# Шаблонами envsubst условие не выразить, поэтому файл с allow/deny
# собирается здесь, до подстановки шаблонов (скрипт 20-envsubst-*).
# ADMIN_ALLOW_CIDRS пуст — админка закрыта для всех (403): на публичном
# адресе вход в неё не должен быть открыт по умолчанию.
set -eu

out=/etc/nginx/conf.d/admin-allow.inc
: > "$out"
for cidr in $(echo "${ADMIN_ALLOW_CIDRS:-}" | tr ',' ' '); do
    # Только адреса и маски: значение попадает в конфигурацию nginx как есть.
    case "$cidr" in
        *[!0-9a-fA-F.:/]*) echo "ADMIN_ALLOW_CIDRS: недопустимое значение '$cidr'" >&2; exit 1 ;;
    esac
    echo "allow $cidr;" >> "$out"
done
echo "deny all;" >> "$out"
