"""Проверки конфигурации для `manage.py check --deploy`.

Регистрируются в CompanyConfig.ready(): у пакета config нет своего
приложения, а проверки должны выполняться на каждой площадке.
"""

from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=True)
def check_allowed_hosts(app_configs, **kwargs):
    """«*» в ALLOWED_HOSTS отключает проверку заголовка Host: ссылки сброса
    пароля и приглашения можно было бы собрать с чужим доменом."""
    if settings.DEBUG or "*" not in settings.ALLOWED_HOSTS:
        return []
    return [checks.Error(
        "ALLOWED_HOSTS содержит «*».",
        hint="Перечислите домены приложения и имя сервиса бэкенда.",
        id="vacation.E002",
    )]


@checks.register(checks.Tags.security, deploy=True)
def check_database_tls(app_configs, **kwargs):
    """Без verify-full пароль и персональные данные идут к базе либо открытым
    текстом, либо по TLS без проверки, с тем ли сервером идёт разговор.

    Предупреждение, а не ошибка: локальный docker compose и Kubernetes
    с базой в том же кластере работают без TLS осознанно.
    """
    db = settings.DATABASES["default"]
    if db["ENGINE"] != "django.db.backends.postgresql":
        return []
    sslmode = db.get("OPTIONS", {}).get("sslmode", "prefer")
    if sslmode == "verify-full":
        return []
    return [checks.Warning(
        f"Подключение к PostgreSQL с sslmode={sslmode}: сертификат сервера не проверяется.",
        hint="Для управляемой базы задайте DB_SSLMODE=verify-full и DB_SSLROOTCERT "
             "(путь к корневому сертификату провайдера).",
        id="vacation.W001",
    )]
