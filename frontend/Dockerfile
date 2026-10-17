# --- Сборка ---------------------------------------------------------------
FROM node:22-alpine AS builder

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
FROM nginx:1.27-alpine AS runtime

ENV PORT=8080 \
    BACKEND_URL=http://backend:8000

COPY --from=builder /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Образ nginx сам подставляет переменные окружения в шаблоны из
# /etc/nginx/templates при старте — отдельный скрипт не нужен.
EXPOSE 8080
