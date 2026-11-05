# Настройка: переменные окружения

> Часть комплекта проектной документации. Обзор и порядок чтения — в `README.md`.

Все параметры задаются переменными окружения — одинаково в Docker Compose,
на Railway, в Kubernetes (ConfigMap и Secret) и в облаке. Образец значений
с пояснениями — `.env.example` в корне репозитория. Секретов в репозитории нет.

Любой секрет можно передать файлом — переменной `<ИМЯ>_FILE` с путём
(раздел 7 `20-deploy-cloud.md`).

## 1. Бэкенд

| Переменная | Назначение |
|---|---|
| `SECRET_KEY` | Ключ Django, случайная строка от 50 символов. **Без неё контейнер не запустится** (`ALLOW_INSECURE_SECRET_KEY=1` — только для локальной проверки) |
| `LOGIN_RATE` | Предел попыток входа с одного адреса, по умолчанию `10/min`; `0` — выключить |
| `DEBUG` | По умолчанию `False` |
| `ALLOWED_HOSTS` | Домены через запятую |
| `DATABASE_URL` | `postgres://...`; без неё используется SQLite. Параметры libpq — в строке запроса: `?sslmode=verify-full&sslrootcert=...&target_session_attrs=read-write&connect_timeout=5` |
| `DB_SSLMODE`, `DB_SSLROOTCERT` | Режим TLS к базе и путь к корневому сертификату; сильнее параметров в `DATABASE_URL`. Для управляемой базы — `verify-full`, иначе `manage.py check --deploy` предупредит (`vacation.W001`) |
| `PORT` | Порт сервиса |
| `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` | Нужны, только если фронтенд на другом домене |
| `VITE_API_BASE_URL` | Адрес API. Пусто — относительный путь через прокси nginx |
| `DJANGO_ADMIN_PATH`, `ADMIN_ALLOW_CIDRS` | Путь админки Django (одинаковый у бэкенда и фронтенда) и адреса, которым она открыта через nginx фронтенда. Пусто — 403 всем |
| `SECURE_COOKIES`, `SECURE_HSTS_SECONDS`, `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_AGE` | HTTPS-настройки Django. Cookies только по HTTPS включены при `DEBUG=False` (в compose выключены: там http://localhost); HSTS по умолчанию выключен — включать после проверки домена |
| `SILENCED_SYSTEM_CHECKS` | Осознанно принятые предупреждения `manage.py check --deploy`, через запятую |
| `TRUSTED_PROXY_CIDRS` | Подсети балансировщика перед nginx фронтенда, которым верим в `X-Forwarded-For`. По умолчанию частные сети; на production — сузить |
| `TRUSTED_PROXY_COUNT` | Число прокси перед бэкендом (по умолчанию 1 — nginx фронтенда) |
| `*_FILE` | Секрет из файла вместо переменной: `SECRET_KEY_FILE`, `DATABASE_URL_FILE`, `EMAIL_HOST_PASSWORD_FILE`, `OIDC_CLIENT_SECRET_FILE`, `ADMIN_PASSWORD_FILE` и др. (`docs/20-deploy-cloud.md`, раздел 7) |
| `SECRET_KEY_FALLBACKS` | Прежние ключи на время смены `SECRET_KEY` |
| `LOG_FORMAT`, `LOG_LEVEL` | Журнал в stdout: `json` (по умолчанию) или `text`; почта и токены в журнале маскируются |
| `RUN_MIGRATIONS` | `0` отключает миграции при старте контейнера |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Первый администратор, создаётся при старте, если его ещё нет. Подробно — `docs/14-initial-admin.md` |
| `ADMIN_FULL_NAME`, `ADMIN_EMPLOYEE_NUMBER`, `ADMIN_HIRE_DATE` | Необязательные поля администратора, есть значения по умолчанию |
| `DEMO_MODE_ENABLED` | Кнопка «Демо-режим» на экране входа. По умолчанию выключено |
| `DEMO_DATABASE_URL`, `DEMO_LOAD_ON_START` | Отдельная база демо (по умолчанию SQLite в контейнере) и загрузка при старте. Подробно — `docs/15-demo-mode.md` |
| `SECRET_KEY_FILE`, `DATABASE_URL_FILE` и др. | См. `*_FILE` выше |
| `MAIL_NOTIFICATIONS_ENABLED`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`, `APP_BASE_URL`, `MAIL_SCHEDULER` | Почтовые уведомления — `12-mail-setup.md` |
| `SSO_ALLOW_PASSWORD_LOGIN`, `KERBEROS_*`, `KRB5_KTNAME`, `OIDC_*` | Доменная аутентификация — `11-sso-setup.md` |
| `DEMO_RESET_DAILY`, `DEMO_RESET_TIME` | Ежедневный сброс демо-данных — `15-demo-mode.md` |
| `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT` | Число процессов и таймаут gunicorn (по умолчанию 3 и 60 с) |

## 2. Фронтенд (nginx)

| Переменная | Назначение |
|---|---|
| `BACKEND_URL` | Адрес бэкенда без пути и завершающего «/», по умолчанию `http://backend:8000` |
| `DJANGO_ADMIN_PATH`, `ADMIN_ALLOW_CIDRS` | Путь админки и адреса, которым она открыта; пусто — 403 всем |
| `TRUSTED_PROXY_CIDRS` | Подсети балансировщика, которым nginx верит в `X-Forwarded-For` (по умолчанию частные сети) |
| `HSTS_MAX_AGE` | Заголовок Strict-Transport-Security, секунды; 0 — выключен |
| `CSP_CONNECT_SRC` | Адрес API для CSP, если он на другом домене |
| `AUTH_RATE_LIMIT`, `AUTH_RATE_BURST` | Предел запросов ко входу с адреса (30/мин) и допустимый всплеск (10) |
| `CLIENT_MAX_BODY_SIZE` | Предельный размер запроса, по умолчанию `5m` |
| `VITE_API_BASE_URL` | При сборке: адрес API на другом домене; пусто — через прокси nginx |

## 3. Резервное копирование

| Переменная | Назначение |
|---|---|
| `BACKUP_INTERVAL_HOURS`, `BACKUP_KEEP_DAYS`, `BACKUP_HOST_DIR` | Docker Compose: период, срок хранения, каталог на хосте |
| `BACKUP_AGE_RECIPIENTS` | Открытые ключи age; задан — копии шифруются |
| `BACKUP_REQUIRE_ENCRYPTION` | `1` — без ключа копия не создаётся (Kubernetes) |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`, `S3_ENDPOINT`, `S3_SSE`, `S3_SSE_KMS_KEY_ID`, `AWS_*` | Выгрузка в S3-совместимое хранилище — `16-backups.md`, раздел 3 |
