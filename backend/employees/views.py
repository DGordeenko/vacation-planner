import base64
import binascii

from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.drf import RecordAuditActor
from employees.importers import import_employees
from employees.models import Employee


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        from sso.services import password_login_allowed
        if not password_login_allowed():
            return Response(
                {"detail": "Вход по паролю отключён, используйте доменную учётную запись",
                 "code": "password_login_disabled"},
                status=status.HTTP_403_FORBIDDEN)

        user = authenticate(
            request,
            username=request.data.get("email"),
            password=request.data.get("password"),
        )
        if user is None:
            return Response(
                {"detail": "Неверный email или пароль", "code": "invalid_credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "employee_id": str(user.id)})


class InvitationAcceptView(APIView):
    """Вход по ссылке-приглашению.

    Учётные записи создаются заранее из CSV — самостоятельная регистрация
    с нуля для 100-500 человек была бы барьером.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        token_value = request.data.get("invitation_token")
        password = request.data.get("password")
        if not token_value or not password:
            return Response(
                {"detail": "Нужны токен приглашения и пароль"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employee = Employee.objects.filter(invitation_token=token_value).first()
        if employee is None:
            return Response(
                {"detail": "Приглашение недействительно", "code": "invalid_invitation"},
                status=status.HTTP_404_NOT_FOUND,
            )

        employee.set_password(password)
        employee.invitation_token = None
        employee.save(update_fields=["password", "invitation_token"])

        token, _ = Token.objects.get_or_create(user=employee)
        return Response({"token": token.key, "employee_id": str(employee.id)})


class AuthMeView(APIView):
    def patch(self, request):
        """Настройки, которыми сотрудник управляет сам."""
        user = request.user
        if "email_notifications_enabled" in request.data:
            user.email_notifications_enabled = bool(
                request.data["email_notifications_enabled"])
            user.save(update_fields=["email_notifications_enabled"])
        return self.get(request)

    def get(self, request):
        user = request.user
        manager = user.manager
        return Response({
            "id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
            "employee_number": user.employee_number,
            "hire_date": user.hire_date,
            "org_unit": user.org_unit.name if user.org_unit else None,
            "is_admin": user.is_admin,
            "is_manager": user.managed_units.exists(),
            "manager_name": manager.full_name if manager else None,
            "tags": [et.tag.name for et in user.employee_tags.select_related("tag")],
            "email_notifications_enabled": user.email_notifications_enabled,
        })


class IsCompanyAdmin(BasePermission):
    message = "Загружать сотрудников может только администратор"

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.is_admin)


class EmployeeImportView(APIView):
    """Загрузка кадровой выгрузки из CSV (ФЗ 4.2.1, UX 2.5).

    `dry_run=true` — предпросмотр: загрузка выполняется целиком и откатывается,
    поэтому администратор видит все проблемы и итоговые счётчики до применения.
    Загрузка применяется целиком или не применяется вовсе.
    """

    permission_classes = [RecordAuditActor, IsCompanyAdmin]

    def post(self, request):
        content = request.data.get("content")
        if not content or not isinstance(content, str):
            return Response({"detail": "Не передано содержимое файла"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            raw = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            return Response({"detail": "Не удалось разобрать файл"},
                            status=status.HTTP_400_BAD_REQUEST)

        dry_run = bool(request.data.get("dry_run"))
        report = import_employees(raw, dry_run=dry_run)
        payload = {
            "applied": report.applied and not dry_run,
            "dry_run": dry_run,
            "total_rows": report.total_rows,
            "created": report.created,
            "updated": report.updated,
            "problems": [p.__dict__ for p in report.problems],
            "warnings": [w.__dict__ for w in report.warnings],
        }
        return Response(payload, status=status.HTTP_200_OK if report.applied
                        else status.HTTP_409_CONFLICT)
