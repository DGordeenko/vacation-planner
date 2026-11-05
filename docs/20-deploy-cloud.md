# Размещение в облаке: требования безопасности

Документ дополняется по мере подготовки к облачной площадке
(serverless-контейнеры или управляемый Kubernetes). Инструкции для
конкретных площадок — `17-deploy-railway.md` и `18-deploy-kubernetes.md`.

## 1. Проверка настроек: `manage.py check --deploy`

CI выполняет `python manage.py check --deploy --fail-level WARNING`
с production-значениями переменных: любое новое предупреждение ломает сборку.
На площадке ту же команду стоит выполнить в контейнере бэкенда после
первого развёртывания.

Осознанные исключения (`SILENCED_SYSTEM_CHECKS`):

| Код | Почему допустимо |
|---|---|
| `security.W008` | На HTTPS перенаправляет балансировщик или ingress. Редирект в Django ломал бы пробы платформы, которые обращаются к поду по HTTP |
| `security.W005` | HSTS на поддомены включается, только если все поддомены компании работают по HTTPS: иначе браузеры перестанут их открывать |
| `security.W021` | Включение в preload-список браузеров практически необратимо: решается отдельно, не настройкой по умолчанию |

Собственные проверки проекта:

| Код | Что проверяет |
|---|---|
| `vacation.W001` | Подключение к PostgreSQL без `sslmode=verify-full` |
| `vacation.E002` | `*` в `ALLOWED_HOSTS` |

Длину и случайность `SECRET_KEY` проверяет встроенная проверка Django
`security.W009` (не меньше 50 символов и 5 разных). Слабый ключ также
останавливает запуск контейнера (`backend/entrypoint.sh`).

## 2. HTTPS

TLS завершается до приложения. nginx фронтенда передаёт бэкенду схему
исходного запроса из `X-Forwarded-Proto` балансировщика, Django доверяет
этому заголовку (`SECURE_PROXY_SSL_HEADER`). Поэтому бэкенд нельзя
публиковать наружу напрямую, минуя nginx или ingress.

`SECURE_HSTS_SECONDS` по умолчанию `0`. После того как домен стабильно
работает по HTTPS, задайте `31536000`.

## 3. Адрес клиента

Адрес клиента в приложении используется в ограничении попыток входа
(`LOGIN_RATE`). Журнал аудита адресов не хранит.

Цепочка: клиент → балансировщик платформы → nginx фронтенда → бэкенд.

- **nginx** доверяет `X-Forwarded-For` только от адресов из
  `TRUSTED_PROXY_CIDRS` (модуль realip, `real_ip_recursive`): берётся
  самый правый адрес, не принадлежащий доверенным прокси, — тот, что
  добавил балансировщик. Всё, что клиент дописал в заголовок сам, левее
  и отбрасывается.
- Бэкенду nginx передаёт **один** адрес — клиента — в `X-Real-IP`
  и `X-Forwarded-For`, а не цепочку.
- **Django** (DRF, `NUM_PROXIES=1`) берёт последний адрес из
  `X-Forwarded-For`, то есть поставленный nginx. Бэкенд не должен быть
  доступен наружу в обход nginx: иначе заголовок можно подделать.

По умолчанию `TRUSTED_PROXY_CIDRS` — частные сети (`10.0.0.0/8`,
`172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`, `fc00::/7`): так
работает «из коробки» на Railway, в Kubernetes и docker compose. На
production сузьте список до подсетей балансировщика платформы.
Слишком узкий список приведёт к тому, что все клиенты будут видны с
адреса балансировщика и разделят один предел попыток входа.

## 4. Автоматическая выкатка (управляемый Kubernetes)

После зелёного CI на `main` workflow `.github/workflows/deploy.yml`
собирает образы с тегом `sha-<7 знаков коммита>`, публикует их в реестр
и выкатывает в кластер:

```
CI зелёный ─▶ образы ─▶ копия базы ─▶ миграции ─▶ бэкенд ─▶ фронтенд ─▶ smoke
                                          │ сбой: стоп   └── сбой: возврат прежних образов
```

Схема одна для **AWS (EKS)**, **Huawei Cloud (CCE)** и **Yandex Cloud
(Managed Service for Kubernetes)**. От облака зависят только вход в реестр
образов и в кластер, их выбирают переменные `REGISTRY_PROVIDER` и
`CLUSTER_AUTH`. Шаги выкатки — `deploy/ci/deploy.sh`, сборка манифестов —
`deploy/ci/render.py`; логика проверяется в CI на заглушках.

Пока в репозитории не задана переменная `DEPLOY_ENABLED=true`, workflow
не запускается.

### 4.1. Что подготовить в облаке (общее для всех)

1. **Кластер Kubernetes** 1.27+ с ingress-контроллером и выпуском
   сертификатов (cert-manager или менеджер сертификатов облака).
   **API кластера должен быть доступен с раннеров GitHub** (публичный
   адрес с ограничением по сетям). Если API только во внутренней сети —
   нужен self-hosted runner внутри неё; тогда в workflow меняется только
   `runs-on`.
2. **Управляемый PostgreSQL** с TLS. Корневой сертификат провайдера —
   в ConfigMap (монтируется в `/app/certs`):
   ```bash
   kubectl create namespace vacation
   kubectl -n vacation create configmap vacation-db-ca --from-file=root.crt=<файл сертификата>
   ```
   В `CONFIG_OVERRIDES` (ниже): `DB_SSLMODE=verify-full`,
   `DB_SSLROOTCERT=/app/certs/root.crt`.
3. **Реестр образов** (ECR, SWR или Container Registry) с репозиториями
   `backend`, `frontend` и `backup` и **сканированием уязвимостей при загрузке**
   (scan on push в ECR, сканер в Container Registry, сканирование образов
   SWR).
4. **Бакет для копий базы** и ключи age — `16-backups.md`, раздел 3:
   S3-совместимое хранилище облака с шифрованием KMS и правилом жизненного
   цикла, Secret `vacation-backup` с ключом доступа только на запись,
   открытый ключ age — в `BACKUP_AGE_RECIPIENTS` (`CONFIG_OVERRIDES`).
   Копия перед миграциями без ключа age не создаётся и останавливает
   выкатку; `BACKUP_ENABLED=0` выключает её, тогда восстановление —
   из ежедневных копий и PITR управляемой базы.
5. **Секрет приложения** `vacation-secrets` — по
   `deploy/k8s/secret.example.yaml` (`docs/18-deploy-kubernetes.md`).
6. **Учётная запись конвейера в кластере** — права только в пространстве
   имён `vacation`, секреты читать не может:
   ```bash
   kubectl apply -f deploy/k8s/pipeline/deployer-rbac.yaml
   ```

### 4.2. Вход в облако: по провайдерам

Принцип — **без долгоживущих ключей**, где облако это позволяет:
GitHub выдаёт задаче OIDC-токен, облако меняет его на временные права
сервисной роли. Доверие ограничивается репозиторием **и** Environment:
`sub = repo:<владелец>/<репозиторий>:environment:production`, — запуск
из другой ветки или форка права не получит.

**AWS.** И реестр, и кластер — по OIDC.
1. IAM → Identity providers: OIDC-провайдер
   `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.
2. Роль с доверием этому провайдеру и условием на `sub` (выше).
   Права: push в два репозитория ECR, `eks:DescribeCluster` для кластера.
3. EKS → Access entries: запись для роли с группой Kubernetes
   `vacation-deployers` и привязка роли `deployer` к группе:
   ```bash
   kubectl -n vacation create rolebinding deployer-group --role=deployer --group=vacation-deployers
   ```
4. Переменные: `REGISTRY_PROVIDER=aws`, `CLUSTER_AUTH=aws-eks`,
   `AWS_ROLE_ARN`, `AWS_REGION`, `EKS_CLUSTER`,
   `REGISTRY=<аккаунт>.dkr.ecr.<регион>.amazonaws.com/vacation`.

**Yandex Cloud.** Реестр — по OIDC (Workload Identity Federation),
кластер — kubeconfig учётной записи `deployer`.
1. IAM → Workload identity federations: issuer
   `https://token.actions.githubusercontent.com`, JWKS
   `https://token.actions.githubusercontent.com/.well-known/jwks`,
   audience — например `https://github.com/<владелец>`.
2. Сервисный аккаунт с ролью `container-registry.images.pusher` на реестр;
   привязка к федерации с `sub` (выше).
3. Переменные: `REGISTRY_PROVIDER=yandex`, `CLUSTER_AUTH=kubeconfig`,
   `YC_SERVICE_ACCOUNT_ID`, `YC_OIDC_AUDIENCE` (тот же, что в федерации),
   `REGISTRY=cr.yandex/<id реестра>`; секрет `KUBECONFIG` (4.3).

**Huawei Cloud.** Реестр — по логину, кластер — kubeconfig `deployer`.
У Huawei есть обмен OIDC-токена на временные права
(`AssumeAgencyWithOIDC`), но вход в SWR по ним требует подписанных
вызовов API и отдельной утилиты — для первой версии выбран вход по
логину. Чтобы ключ не давал лишнего:
1. Отдельный IAM-пользователь **только** с правом загрузки в
   организацию SWR, без доступа к консоли.
2. SWR → «Долговременная команда входа» для него: из неё логин
   (`<регион>@<AK>`) и пароль — в секреты `REGISTRY_USERNAME`,
   `REGISTRY_PASSWORD`. Ротация — раз в 90 дней (новый AK, старый удалить).
3. Переменные: `REGISTRY_PROVIDER=password`, `CLUSTER_AUTH=kubeconfig`,
   `REGISTRY_HOST=swr.<регион>.myhuaweicloud.com`,
   `REGISTRY=swr.<регион>.myhuaweicloud.com/<организация>`; секрет
   `KUBECONFIG` (4.3).

Тот же вариант `password` подходит для любого другого реестра (Harbor,
Nexus).

### 4.3. kubeconfig для конвейера

Для `CLUSTER_AUTH=kubeconfig` — файл с токеном учётной записи `deployer`.
Почему не облачный вход в кластер: у каждого облака он свой и требует его
утилиты на раннере, а токен Kubernetes одинаков везде и даёт права только
в `vacation`. Отзыв — удалить Secret `deployer-token` (раз в полгода или
при подозрении на утечку — пересоздать и обновить секрет в GitHub).

```bash
NS=vacation
SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA=$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
TOKEN=$(kubectl -n $NS get secret deployer-token -o jsonpath='{.data.token}' | base64 -d)
cat > deployer.kubeconfig <<KCFG
apiVersion: v1
kind: Config
clusters: [{name: vacation, cluster: {server: $SERVER, certificate-authority-data: $CA}}]
users: [{name: deployer, user: {token: $TOKEN}}]
contexts: [{name: deployer, context: {cluster: vacation, user: deployer, namespace: $NS}}]
current-context: deployer
KCFG
kubectl --kubeconfig deployer.kubeconfig -n $NS auth can-i patch deployments   # yes
kubectl --kubeconfig deployer.kubeconfig -n $NS auth can-i get secrets         # no
```

Содержимое файла — в секрет `KUBECONFIG` Environment, сам файл удалить.

### 4.4. Настройки GitHub

1. Settings → Environments → **production**: Deployment branches —
   только `main`; Required reviewers — по желанию (тогда каждая выкатка
   ждёт подтверждения).
2. Переменные **Environment** (Variables):

   | Переменная | Пример / значение |
   |---|---|
   | `REGISTRY_PROVIDER` | `aws`, `yandex` или `password` |
   | `CLUSTER_AUTH` | `kubeconfig` или `aws-eks` |
   | `REGISTRY` | адрес без имени образа, см. 4.2 |
   | `APP_DOMAIN` | `vacation.example.com` |
   | `APP_URL` | `https://vacation.example.com` |
   | `INGRESS_CLASS` | класс ingress-контроллера; пусто — по умолчанию |
   | `INGRESS_ANNOTATIONS` | по строке `ключ=значение`: выпуск сертификата, тип балансировщика |
   | `CONFIG_OVERRIDES` | по строке `КЛЮЧ=значение` в ConfigMap: `DB_SSLMODE`, `DB_SSLROOTCERT`, `TRUSTED_PROXY_CIDRS`, `ADMIN_ALLOW_CIDRS`, `SECURE_HSTS_SECONDS`… |
   | `BACKUP_ENABLED` | `1` (по умолчанию) или `0` |
   | `SMOKE_ADMIN_CODE` | ожидаемый код `/admin/login/` с раннера, по умолчанию `403` |
   | `ADMIN_PATH` | если менялся `DJANGO_ADMIN_PATH` |
   | провайдерные | `AWS_ROLE_ARN`, `AWS_REGION`, `EKS_CLUSTER`, `YC_SERVICE_ACCOUNT_ID`, `YC_OIDC_AUDIENCE`, `REGISTRY_HOST` |

3. Секреты **Environment**: `KUBECONFIG`; для `password` —
   `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`.
4. Переменная **репозитория** (не Environment) `DEPLOY_ENABLED=true` —
   включает выкатку.

### 4.5. Первый запуск, повтор и откат

- Первый запуск: Actions → **Deploy** → Run workflow, тег пустой.
  Job миграций создаст первого администратора (`ADMIN_EMAIL` и
  `ADMIN_PASSWORD` в `vacation-secrets`); после первого входа удалите их
  из секрета.
- Дальше — автоматически после каждого зелёного CI на `main`.
- **Откат на прежнюю версию**: Run workflow с тегом этой версии
  (`sha-…`, теги видны в итогах прошлых запусков и в реестре). Образы
  не пересобираются; миграции этой версии уже применены — Job пройдёт
  без изменений.
- Миграции **назад не откатываются**. Если новая схема несовместима со
  старым кодом — восстановление из копии `pre-deploy/` (`16-backups.md`).

## 5. nginx фронтенда

Заголовки безопасности ставит nginx (`frontend/nginx/security-headers.inc.template`)
на каждый ответ, включая ответы бэкенда и ошибки; одноимённые заголовки
Django скрываются, чтобы источник был один.

| Заголовок | Значение и почему |
|---|---|
| `Content-Security-Policy` | Всё со своего домена; страницу нельзя встроить в чужой сайт. Временное исключение — Google Fonts, до перевода шрифта в локальные файлы. `CSP_CONNECT_SRC` — адрес API на другом домене |
| `Strict-Transport-Security` | `HSTS_MAX_AGE`, по умолчанию 0. Включать вместе с `SECURE_HSTS_SECONDS` бэкенда |
| `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` | Запрет встраивания, угадывания типа файла, утечки адреса страницы, доступа к камере и т. п. |

- **Кэш:** `index.html` — `no-cache` (после выкатки браузер сразу получает
  новую версию), `/assets/` — на год (имя файла меняется с содержимым).
- **Перебор паролей:** вход, приглашения, демо-вход и начало доменного
  входа — не больше `AUTH_RATE_LIMIT` (30 в минуту) с адреса, всплеск
  до `AUTH_RATE_BURST` (10), дальше 429. Если сотрудники выходят в
  интернет через один адрес офиса, а приложение доступно снаружи —
  увеличьте предел. Django ограничивает сами неудачные попытки
  (`LOGIN_RATE`).
- `client_max_body_size` — `CLIENT_MAX_BODY_SIZE` (5 МБ), версия nginx
  в ответах скрыта.
- Образ — `nginx-unprivileged`: nginx работает от пользователя с UID 101,
  не от root.

## 6. Образы

- **Базовые образы закреплены по digest** (`python:3.12-slim@sha256:…`):
  тег переписывается при каждом выпуске, а digest — нет, и одна ревизия
  всегда собирается из одного и того же. Обновления digest, зависимостей
  pip и npm, actions и образов compose предлагает **Dependabot**
  (`.github/dependabot.yml`) еженедельными PR.
- **`.dockerignore`** не пускает в образ `.env*`, ключи, локальные базы
  (`db.sqlite3`, `testdata/`, дампы — в них бывают настоящие ПДн) и тесты.
- **`HEALTHCHECK` в образах не задан** намеренно: площадки проверяют
  контейнеры сами — Kubernetes пробами `/api/health/live/` и
  `/api/health/ready/`, docker compose — своим `healthcheck`, Railway —
  Healthcheck Path. Собственный `HEALTHCHECK` образа Kubernetes
  игнорирует, а в compose он дублировал бы уже заданную проверку.

### Проверки безопасности в CI

| Проверка | Что ищет | Когда CI падает |
|---|---|---|
| Trivy | Уязвимости в образах (ОС и библиотеки) | CRITICAL или HIGH, для которых есть исправление |
| pip-audit | Уязвимые Python-зависимости (`backend/requirements.txt`) | Любая известная уязвимость, кроме исключений ниже |
| npm audit | Уязвимые зависимости бандла фронтенда | high и выше |
| gitleaks | Ключи, пароли, токены во всей истории git | Любая находка |

SBOM обоих образов (SPDX) прикладывается к каждому запуску CI —
артефакт `sbom`.

Исключения (каждое — с причиной, пересматривать при обновлениях):

| Код | Пакет | Почему |
|---|---|---|
| `PYSEC-2025-183` | pyjwt | Оспорен поставщиком, исправления нет: длину ключа выбирает приложение; у нас ключи RS256 от Keycloak |

### Файловая система только для чтения

Во всех подах приложения (бэкенд, фронтенд, Job миграций, CronJob почты)
`readOnlyRootFilesystem: true`: во взломанном контейнере нельзя подменить
код или доставить инструменты. Писать можно только во временные тома:

| Под | Каталог | Зачем |
|---|---|---|
| бэкенд, Job, CronJob | `/tmp` (диск, 256 МБ) | служебные файлы gunicorn и Python, демо-база SQLite |
| фронтенд | `/etc/nginx/conf.d` (память, 1 МБ) | конфигурация nginx, собираемая при старте из переменных |
| фронтенд | `/tmp` (память, 64 МБ) | pid и временные файлы проксирования |

CI запускает compose с теми же ограничениями (`deploy/ci/compose.readonly.yml`,
с включённым демо-режимом) и проверяет, что приложение работает,
а запись в корневую ФС невозможна.

## 7. Секреты

### Переменная или файл

Каждый секрет можно передать файлом — переменной `<ИМЯ>_FILE` с путём:
`SECRET_KEY_FILE`, `SECRET_KEY_FALLBACKS_FILE`, `DATABASE_URL_FILE`,
`DEMO_DATABASE_URL_FILE`, `EMAIL_HOST_PASSWORD_FILE`,
`OIDC_CLIENT_SECRET_FILE`, `ADMIN_PASSWORD_FILE`. Файл сильнее переменной.

Файл предпочтительнее: переменные окружения видны в `/proc/<pid>/environ`,
`docker inspect`, `kubectl describe`, дампах при падении. Указанный,
но нечитаемый файл останавливает запуск — тихий откат на значение по
умолчанию был бы хуже.

### Где хранить в облаке

Источник истины — сервис секретов облака, в кластер секреты попадают
автоматически, в репозиторий и в GitHub — никогда.

| Облако | Сервис секретов | Как попадает в под |
|---|---|---|
| AWS | Secrets Manager | External Secrets Operator → Secret `vacation-secrets` |
| Yandex Cloud | Lockbox | External Secrets Operator (есть в Marketplace) → Secret `vacation-secrets` |
| Huawei Cloud | DEW / CSMS | Дополнение CCE Secrets Manager for DEW монтирует секреты **файлами** → переменные `*_FILE` |

Для External Secrets Operator манифесты приложения не меняются: оператор
создаёт тот же Secret `vacation-secrets`, что сейчас создаётся вручную.
Для Huawei — том CSI в поде бэкенда, например `/mnt/secrets/`, и в
ConfigMap: `SECRET_KEY_FILE=/mnt/secrets/secret_key`,
`DATABASE_URL_FILE=/mnt/secrets/database_url` и т. д.

Доступ к секретам — отдельной учётной записью только на чтение нужных
секретов (`lockbox.payloadViewer` на конкретный секрет, политика
Secrets Manager на конкретные ARN, `cce.io/dew-resource` со списком
секретов). У конвейера выкатки доступа к секретам приложения нет.

### Смена SECRET_KEY

От `SECRET_KEY` зависят только подписи сессий Django: вход в админку и
незавершённый вход через Keycloak. Токены входа в приложение — случайные
ключи в базе, от ключа не зависят. Сменить без разлогинивания:

1. Новый ключ — в `SECRET_KEY`, прежний — в `SECRET_KEY_FALLBACKS`.
2. Выкатить. Старые сессии принимаются, новые подписываются новым ключом.
3. Через `SESSION_COOKIE_AGE` (12 часов) удалить `SECRET_KEY_FALLBACKS`.

При утечке ключа шаг 1 — без `SECRET_KEY_FALLBACKS`: все сессии,
подписанные утёкшим ключом, сразу перестают действовать.

### Первый администратор

Пароль первого администратора удаляется из окружения после первого
входа — обязательный шаг, чек-лист в `14-initial-admin.md`, раздел 5.
В Kubernetes он в отдельном Secret `vacation-admin`, который подключён
только к Job миграций.

## 8. Журналы и персональные данные

- **Формат** — JSON в stdout (`LOG_FORMAT=json`, по умолчанию): площадки
  и облачные сервисы журналов разбирают его на поля без настройки.
  docker compose пишет текстом (`LOG_FORMAT=text`).
- **ПДн в журнал не пишутся.** Код пишет идентификаторы, а не ФИО и
  почту; адреса почты, попавшие в сообщение случайно (в тексте
  исключения, в аргументах), фильтр маскирует: `ivan@corp.ru` →
  `i***@corp.ru` (`backend/config/logs.py`).
- **Токены.** Ссылка-приглашение (`?invite=…`) и возврат из Keycloak
  (`?code=…`) несут токены в адресе, а браузер отправляет адрес страницы
  в `Referer` каждого запроса к API. Поэтому журналы запросов nginx
  и gunicorn пишут **только путь** — без строки запроса и без `Referer`.
  Значения `token=`, `invite=`, `code=`, `state=`, `password=` в любом
  сообщении маскируются. CI проверяет, что токены из адреса в журналы
  не попадают.
- **Логгер `security`** — неудачные входы по паролю (почта маскирована)
  и превышение предела попыток (с адресом клиента). По полю `event`
  (`login_failed`, `login_throttled`) удобно строить оповещения.
- **Ошибки 500** — стек и текст исключения только в журнал, клиент
  получает общий ответ (`DEBUG=False` во всех профилях; тест
  `config/tests_logs.py`).
- **Срок хранения журналов** задаётся в сервисе журналов облака; так как
  ПДн в них нет, достаточно срока, нужного для разбора инцидентов
  (например, 30–90 дней).
