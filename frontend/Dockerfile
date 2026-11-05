# --- Сборка ---------------------------------------------------------------
FROM node:26-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94 AS builder

WORKDIR /build
COPY package*.json ./
RUN npm ci

COPY . .
# Адрес API попадает в бандл на этапе сборки. Пусто — относительный путь
# и проксирование через nginx; задано — абсолютный адрес (Railway).
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# --- Отдача ---------------------------------------------------------------
# Непривилегированный вариант официального образа: nginx работает от
# пользователя nginx (UID 101), а не от root. Взлом nginx не даёт root
# в контейнере; Kubernetes проверяет это (runAsNonRoot).
# Стабильная ветка nginx (чётная): 1.27 больше не поддерживается, и в её
# образе Trivy находил 61 исправимую уязвимость.
FROM nginxinc/nginx-unprivileged:1.30-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS runtime

# NGINX_ENTRYPOINT_LOCAL_RESOLVERS: образ nginx при старте записывает
# адреса DNS из /etc/resolv.conf в NGINX_LOCAL_RESOLVERS для директивы
# resolver в nginx/default.conf.template. BACKEND_URL — без пути и завершающего «/».
ENV PORT=8080 \
    BACKEND_URL=http://backend:8000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    DJANGO_ADMIN_PATH=admin \
    ADMIN_ALLOW_CIDRS="" \
    TRUSTED_PROXY_CIDRS="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,fc00::/7" \
    CSP_CONNECT_SRC="" \
    HSTS_MAX_AGE=0 \
    CLIENT_MAX_BODY_SIZE=5m \
    AUTH_RATE_LIMIT=30r/m \
    AUTH_RATE_BURST=10

# Исправления пакетов Alpine, вышедшие после сборки базового образа:
# образ nginx пересобирается реже, чем выходят исправления (Trivy находил
# в нём CRITICAL brotli и HIGH curl). Цена — сборка зависит от дня; версия
# nginx и сам базовый образ при этом закреплены digest.
USER root
RUN apk upgrade --no-cache
USER 101

COPY --from=builder /build/dist /usr/share/nginx/html
# Шаблоны: переменные окружения подставляются при старте, результат —
# в /etc/nginx/conf.d (default.conf, security-headers.inc).
COPY nginx/default.conf.template nginx/security-headers.inc.template /etc/nginx/templates/
COPY nginx/proxy.inc /etc/nginx/conf.d/proxy.inc
# Список адресов для админки собирается при старте контейнера.
COPY docker-entrypoint.d/15-admin-allow.sh docker-entrypoint.d/16-trusted-proxies.sh /docker-entrypoint.d/

# Образ nginx сам подставляет переменные окружения в шаблоны из
# /etc/nginx/templates при старте — отдельный скрипт не нужен.
EXPOSE 8080
