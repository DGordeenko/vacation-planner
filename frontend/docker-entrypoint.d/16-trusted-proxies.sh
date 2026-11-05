#!/bin/sh
# Адреса прокси, которым nginx верит в X-Forwarded-For (балансировщик
# платформы, ingress-контроллер). От них nginx берёт адрес клиента
# (модуль realip), от всех остальных — адрес соединения: подделанный
# клиентом заголовок не меняет его адрес в ограничениях и журналах.
# По умолчанию (Dockerfile) — частные сети: в них живут балансировщики
# Railway, Kubernetes и docker. На production сузить до подсетей балансировщика.
set -eu

out=/etc/nginx/conf.d/trusted-proxies.inc
: > "$out"
for cidr in $(echo "${TRUSTED_PROXY_CIDRS:-}" | tr ',' ' '); do
    case "$cidr" in
        *[!0-9a-fA-F.:/]*) echo "TRUSTED_PROXY_CIDRS: недопустимое значение '$cidr'" >&2; exit 1 ;;
    esac
    echo "set_real_ip_from $cidr;" >> "$out"
done
echo "real_ip_header X-Forwarded-For;" >> "$out"
echo "real_ip_recursive on;" >> "$out"
