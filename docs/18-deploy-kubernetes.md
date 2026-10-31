# Развёртывание в Kubernetes

> Часть комплекта проектной документации. Обзор и порядок чтения — в `README.md`.

Манифесты — в `deploy/k8s/`, собираются kustomize. Образы те же, что для
Docker и Railway. CI на каждый PR собирает манифесты и проверяет их по схемам
Kubernetes 1.29.

---

## 1. Что разворачивается

| Ресурс | Назначение |
|---|---|
| `Deployment backend` (2 реплики) + `Service backend:8000` | API, gunicorn. Пробы на `/api/health/` |
| `Deployment frontend` (2 реплики) + `Service frontend:8080` | Интерфейс, nginx; `/api/` проксирует на `backend:8000` |
| `Ingress vacation` | Один домен, всё на frontend |
| `Job migrate` | Миграции и первый администратор до обновления подов |
| `CronJob mail-urgent` | Срочные письма каждые 5 минут |
| `CronJob mail-digest` | Дневная сводка по будням в 9:00 МСК |
| `ConfigMap vacation-config` | Несекретные настройки |
| `Secret vacation-secrets` | Создаётся вручную по `secret.example.yaml`, в репозиторий не попадает |

База PostgreSQL — внешняя (управляемый сервис или отдельный чарт), адрес —
в `DATABASE_URL` секрета.

**Почему фоновые процессы в подах выключены.** В Docker Compose и на Railway
одна реплика бэкенда сама отправляет почту и сбрасывает демо по расписанию.
При двух репликах это делалось бы дважды, поэтому в ConfigMap
`MAIL_SCHEDULER=0`, `DEMO_RESET_DAILY=0`, `RUN_MIGRATIONS=0`, а работу
выполняют Job и CronJob.

## 2. Первый запуск

1. Собрать и загрузить образы в свой реестр:
   ```bash
   docker build -t registry.example.com/vacation/backend:1.2.0 backend
   docker build -t registry.example.com/vacation/frontend:1.2.0 frontend
   docker push registry.example.com/vacation/backend:1.2.0
   docker push registry.example.com/vacation/frontend:1.2.0
   ```
2. В `deploy/k8s/kustomization.yaml` указать реестр и версию (`images`),
   в `configmap.yaml` и `ingress.yaml` — домен вместо `vacation.example.com`.
3. Создать пространство имён и секрет:
   ```bash
   kubectl create namespace vacation
   cp deploy/k8s/secret.example.yaml secret.yaml   # заполнить, не коммитить
   kubectl -n vacation apply -f secret.yaml
   ```
4. Применить манифесты: `kubectl apply -k deploy/k8s`.
   Job `migrate` применит миграции и создаст первого администратора.
5. Проверить: `kubectl -n vacation get pods`,
   `https://<домен>/api/health/` — `{"status": "ok"}`.

## 3. Обновление версии

```bash
# новая версия в kustomization.yaml (images.newTag), затем:
kubectl -n vacation delete job migrate --ignore-not-found
kubectl apply -k deploy/k8s
kubectl -n vacation wait --for=condition=complete job/migrate --timeout=300s
```

Job нельзя изменить после создания, поэтому старый удаляется перед
применением. Перед обновлением снимите резервную копию (`16-backups.md`).

## 4. Дополнительно

- **Резервное копирование** — CronJob из `16-backups.md`, раздел 3.
- **Демо-режим** в кластере: отдельная база Postgres в `DEMO_DATABASE_URL`,
  `DEMO_LOAD_ON_START=0`; загрузка и ежедневный сброс — Job и CronJob
  с `python manage.py load_demo` (`15-demo-mode.md`, раздел 4).
- **Доменная аутентификация** — `11-sso-setup.md`.
