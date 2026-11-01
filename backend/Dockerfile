# --- Сборка зависимостей -------------------------------------------------
FROM python:3.12-slim AS builder

# Вход по Kerberos нужен не везде, а тянет системные библиотеки MIT Kerberos —
# включается при сборке: docker build --build-arg WITH_KERBEROS=1 (docs/11-sso-setup.md).
ARG WITH_KERBEROS=0
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
# Зависимости ставятся в отдельный слой: пересобираются только при их
# изменении, а не при каждой правке кода.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt \
 && if [ "$WITH_KERBEROS" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends gcc libkrb5-dev \
      && /opt/venv/bin/pip install gssapi==1.9.0 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# --- Образ приложения ----------------------------------------------------
FROM python:3.12-slim AS runtime

ARG WITH_KERBEROS=0

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Запуск не от root: компрометация процесса не должна давать права в контейнере.
RUN useradd --create-home --uid 1000 app \
 && if [ "$WITH_KERBEROS" = "1" ]; then \
      apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
         libgssapi-krb5-2 krb5-user \
      && rm -rf /var/lib/apt/lists/*; \
    fi
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . .

# Проверка проекта при сборке: несовместимые версии зависимостей или ошибка
# импорта должны валить сборку образа, а не запуск на сервере.
# collectstatic ниже этого не ловит — он не загружает URLconf, а значит
# не импортирует представления, где такие ошибки обычно и проявляются.
# DEMO_MODE_ENABLED=0 явно: с включённым демо проверка открыла бы файл
# демо-базы и оставила его в образе от имени root (docs/15-demo-mode.md, разд. 7).
RUN SECRET_KEY=build-only DEMO_MODE_ENABLED=0 python manage.py check

# Статика собирается на этапе сборки: в рантайме том может быть только для чтения.
RUN SECRET_KEY=build-only DEMO_MODE_ENABLED=0 python manage.py collectstatic --noinput

COPY --chown=app:app entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER app
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
