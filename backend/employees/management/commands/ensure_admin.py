"""Создание первого администратора из переменных окружения.

Вызывается в entrypoint.sh при каждом старте контейнера, поэтому команда
идемпотентна: если пользователь с такой почтой уже есть, она ничего
не меняет — ни пароль, ни права. Повышать существующего сотрудника
до администратора или сбрасывать ему пароль при перезапуске сервиса
было бы опасно и неожиданно.

Обязательные переменные: ADMIN_EMAIL, ADMIN_PASSWORD.
Необязательные (у модели Employee есть обязательные поля):
  ADMIN_FULL_NAME        по умолчанию «Администратор»
  ADMIN_EMPLOYEE_NUMBER  по умолчанию «ADMIN-0001»
  ADMIN_HIRE_DATE        ГГГГ-ММ-ДД, по умолчанию — сегодня

Пароль не выводится ни в одном сообщении, включая сообщения об ошибках.
"""

import os
from datetime import date

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from employees.models import Employee

DEFAULT_FULL_NAME = "Администратор"
DEFAULT_EMPLOYEE_NUMBER = "ADMIN-0001"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


class Command(BaseCommand):
    help = "Создаёт администратора из ADMIN_EMAIL и ADMIN_PASSWORD, если его ещё нет"

    def handle(self, *args, **options):
        email = _env("ADMIN_EMAIL")
        # Пароль не обрезается: пробелы по краям могут быть его частью.
        password = os.environ.get("ADMIN_PASSWORD") or ""

        if not email and not password:
            self.stdout.write("ensure_admin: ADMIN_EMAIL и ADMIN_PASSWORD не заданы — пропускаем")
            return

        if not email or not password:
            # Половина настройки — почти наверняка опечатка в окружении.
            # Сервис при этом запускается: без администратора он работоспособен.
            missing = "ADMIN_EMAIL" if not email else "ADMIN_PASSWORD"
            self.stderr.write(self.style.WARNING(
                f"ensure_admin: не задана переменная {missing} — администратор не создан"))
            return

        email = Employee.objects.normalize_email(email)

        # Сравнение без учёта регистра: normalize_email приводит к нижнему
        # регистру только домен, а Ivanov@corp.ru и ivanov@corp.ru —
        # один и тот же человек.
        if Employee.objects.filter(email__iexact=email).exists():
            self.stdout.write(
                f"ensure_admin: пользователь {email} уже существует — ничего не меняем")
            return

        full_name = _env("ADMIN_FULL_NAME") or DEFAULT_FULL_NAME
        employee_number = _env("ADMIN_EMPLOYEE_NUMBER") or DEFAULT_EMPLOYEE_NUMBER
        hire_date = self._hire_date()

        if Employee.objects.filter(employee_number=employee_number).exists():
            raise CommandError(
                f"ensure_admin: табельный номер {employee_number} уже занят другим "
                f"сотрудником. Задайте другой в ADMIN_EMPLOYEE_NUMBER")

        self._check_password(password, email, full_name)

        try:
            with transaction.atomic():
                Employee.objects.create_superuser(
                    email=email,
                    employee_number=employee_number,
                    full_name=full_name,
                    hire_date=hire_date,
                    password=password,
                )
        except IntegrityError:
            # Несколько реплик стартуют одновременно (Kubernetes): между проверкой
            # и созданием администратора могла создать соседняя. Это не ошибка.
            if Employee.objects.filter(email__iexact=email).exists():
                self.stdout.write(
                    f"ensure_admin: пользователь {email} создан параллельным процессом "
                    f"— ничего не меняем")
                return
            raise CommandError(
                "ensure_admin: не удалось создать администратора — нарушено "
                "ограничение уникальности в базе")

        self.stdout.write(self.style.SUCCESS(
            f"ensure_admin: создан администратор {email} (табельный номер {employee_number})"))

    def _hire_date(self) -> date:
        raw = _env("ADMIN_HIRE_DATE")
        if not raw:
            return date.today()
        try:
            return date.fromisoformat(raw)
        except ValueError as error:
            raise CommandError(
                f"ensure_admin: ADMIN_HIRE_DATE «{raw}» не в формате ГГГГ-ММ-ДД") from error

    def _check_password(self, password: str, email: str, full_name: str) -> None:
        """Штатные валидаторы Django из AUTH_PASSWORD_VALIDATORS.

        Учётная запись администратора — самая привилегированная в системе
        и обычно доступна из интернета. Слабый пароль из переменной
        окружения отклоняется до создания пользователя.
        """
        candidate = Employee(email=email, full_name=full_name)
        try:
            validate_password(password, user=candidate)
        except ValidationError as error:
            # Сообщения валидаторов Django не содержат сам пароль.
            # Сообщения валидаторов уже заканчиваются точкой.
            reasons = " ".join(error.messages)
            raise CommandError(
                f"ensure_admin: ADMIN_PASSWORD не прошёл проверку надёжности: {reasons}"
            ) from None
