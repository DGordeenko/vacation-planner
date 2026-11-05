from django.apps import AppConfig


class CompanyConfig(AppConfig):
    name = 'company'

    def ready(self):
        from config import checks  # noqa: F401 — регистрация проверок --deploy
