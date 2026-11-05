"""Справочник тегов, лимиты и ручное назначение (ФЗ 4.2.2, 4.7).

Права (решение от 06.11.2026, меняет ФЗ 4.2.2 и 4.7):
- администратор создаёт, меняет и удаляет любые теги и лимиты;
- руководитель создаёт теги и задаёт им лимит, меняет и удаляет только
  созданные им теги. Лимит тега действует на всю компанию, поэтому чужие
  теги и теги подразделений руководителю не доступны: правка одного
  руководителя не должна менять правила для других подразделений;
- дополнительный тег назначает руководитель любого уровня — сотрудникам
  своей структуры, и администратор — любому. Сотрудник себе теги
  не назначает (решение от 25.10.2026, меняет ФЗ 4.2.2);
- тег по оргструктуре вручную не снимается: он выводится из структуры
  при загрузке кадровой выгрузки.
"""

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee, EmployeeTag, Tag


def is_manager(user):
    return user.managed_units.exists()


def can_manage_tag(user, tag, is_org_unit_tag):
    """Может ли пользователь менять и удалять тег (название, лимит)."""
    if user.is_admin:
        return True
    return (not is_org_unit_tag and tag.created_by_id == user.id and is_manager(user))


class TagSerializer(serializers.ModelSerializer):
    # Счётчики приходят аннотацией запроса; у только что созданного тега их нет.
    members_count = serializers.SerializerMethodField()
    manual_count = serializers.SerializerMethodField()
    is_org_unit_tag = serializers.SerializerMethodField()
    created_by = serializers.CharField(source="created_by.full_name", default=None,
                                       read_only=True)
    # Право решает сервер: интерфейс по этому признаку показывает правку.
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = ["id", "name", "max_simultaneous_absence", "members_count",
                  "manual_count", "is_org_unit_tag", "created_by", "can_edit"]

    def get_can_edit(self, obj):
        request = self.context.get("request")
        return bool(request) and can_manage_tag(
            request.user, obj, self.get_is_org_unit_tag(obj))

    def get_members_count(self, obj):
        return getattr(obj, "members_count", 0)

    def get_manual_count(self, obj):
        return getattr(obj, "manual_count", 0)

    def get_is_org_unit_tag(self, obj):
        return getattr(obj, "auto_count", 0) > 0

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Название тега не может быть пустым")
        return value


class TagMemberSerializer(serializers.ModelSerializer):
    employee_id = serializers.UUIDField(source="employee.id")
    full_name = serializers.CharField(source="employee.full_name")
    employee_number = serializers.CharField(source="employee.employee_number")
    org_unit = serializers.CharField(source="employee.org_unit.name", default=None)
    assigned_by = serializers.CharField(source="assigned_by.full_name", default=None)

    class Meta:
        model = EmployeeTag
        fields = ["employee_id", "full_name", "employee_number", "org_unit",
                  "source", "assigned_by", "assigned_at"]


def can_assign(actor, employee):
    """Кто может назначить или снять дополнительный тег сотруднику."""
    if actor.is_admin:
        return True
    return actor.managed_units.exists() and employee.id in actor.structure_employee_ids()


class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    # Справочник небольшой (десятки тегов) и нужен целиком для фильтров.
    pagination_class = None

    def get_queryset(self):
        return Tag.objects.select_related("created_by").annotate(
            members_count=Count("employee_tags", distinct=True),
            manual_count=Count("employee_tags", distinct=True,
                               filter=Q(employee_tags__source=Tag.Source.MANUAL)),
            auto_count=Count("employee_tags", distinct=True,
                             filter=Q(employee_tags__source=Tag.Source.AUTO_ORG)),
        ).order_by("name")

    def _forbidden(self, request, tag=None):
        if tag is None:
            if request.user.is_admin or is_manager(request.user):
                return None
            return Response({"detail": "Создавать теги может руководитель или администратор"},
                            status=status.HTTP_403_FORBIDDEN)
        if can_manage_tag(request.user, tag, tag.auto_count > 0):
            return None
        return Response({"detail": "Руководитель меняет только созданные им теги; "
                                   "остальные — администратор"},
                        status=status.HTTP_403_FORBIDDEN)

    def create(self, request, *args, **kwargs):
        return self._forbidden(request) or super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def update(self, request, *args, **kwargs):
        return (self._forbidden(request, self.get_object())
                or super().update(request, *args, **kwargs))

    def destroy(self, request, *args, **kwargs):
        tag = self.get_object()
        denied = self._forbidden(request, tag)
        if denied:
            return denied
        if tag.auto_count:
            return Response(
                {"detail": "Тег подразделения удалить нельзя: он восстановится "
                           "при следующей загрузке кадровой выгрузки",
                 "code": "org_unit_tag"},
                status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        tag = self.get_object()
        rows = tag.employee_tags.select_related(
            "employee", "employee__org_unit", "assigned_by").order_by("employee__full_name")
        if not (request.user.is_admin or request.user.is_hr):
            rows = rows.filter(employee_id__in=request.user.visible_employee_ids())
        return Response(TagMemberSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        tag = self.get_object()
        employee = get_object_or_404(Employee, id=request.data.get("employee_id"))
        if not can_assign(request.user, employee):
            return Response({"detail": "Назначать теги может руководитель — "
                                       "сотрудникам своей структуры"},
                            status=status.HTTP_403_FORBIDDEN)
        link, created = EmployeeTag.objects.get_or_create(
            employee=employee, tag=tag,
            defaults={"source": Tag.Source.MANUAL, "assigned_by": request.user})
        return Response(TagMemberSerializer(link).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=["delete"], url_path=r"unassign/(?P<employee_id>[^/.]+)")
    def unassign(self, request, pk=None, employee_id=None):
        tag = self.get_object()
        employee = get_object_or_404(Employee, id=employee_id)
        if not can_assign(request.user, employee):
            return Response({"detail": "Снимать теги может руководитель — "
                                       "сотрудникам своей структуры"},
                            status=status.HTTP_403_FORBIDDEN)
        link = get_object_or_404(EmployeeTag, employee=employee, tag=tag)
        if link.source == Tag.Source.AUTO_ORG:
            return Response(
                {"detail": "Тег по оргструктуре снимается только переводом сотрудника",
                 "code": "org_unit_tag"},
                status=status.HTTP_409_CONFLICT)
        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeSearchView(APIView):
    """Поиск сотрудника для назначения тега — в пределах видимости."""

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        employees = Employee.objects.filter(
            status=Employee.Status.ACTIVE).select_related("org_unit")
        if not request.user.is_admin:
            employees = employees.filter(id__in=request.user.visible_employee_ids())
        if query:
            employees = employees.filter(
                Q(full_name__icontains=query) | Q(employee_number__icontains=query)
                | Q(email__icontains=query))
        return Response([
            {"id": str(e.id), "full_name": e.full_name, "employee_number": e.employee_number,
             "org_unit": e.org_unit.name if e.org_unit else None}
            for e in employees.order_by("full_name")[:20]
        ])
