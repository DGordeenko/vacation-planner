from django.apps import AppConfig


class MailerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mailer"
    verbose_name = "Почтовые уведомления"

    def ready(self):
        from mailer import signals

        signals.connect()
