"""Справочник тегов, лимиты и ручное назначение (ФЗ 4.2.2, 4.7).

Права:
- справочник и лимиты меняет администратор;
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


class TagSerializer(serializers.ModelSerializer):
    # Счётчики приходят аннотацией запроса; у только что созданного тега их нет.
    members_count = serializers.SerializerMethodField()
    manual_count = serializers.SerializerMethodField()
    is_org_unit_tag = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = ["id", "name", "max_simultaneous_absence", "members_count",
                  "manual_count", "is_org_unit_tag"]

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
    return actor.managed_units.exists() and employee.id in actor.visible_employee_ids()


class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    # Справочник небольшой (десятки тегов) и нужен целиком для фильтров.
    pagination_class = None

    def get_queryset(self):
        return Tag.objects.annotate(
            members_count=Count("employee_tags", distinct=True),
            manual_count=Count("employee_tags", distinct=True,
                               filter=Q(employee_tags__source=Tag.Source.MANUAL)),
            auto_count=Count("employee_tags", distinct=True,
                             filter=Q(employee_tags__source=Tag.Source.AUTO_ORG)),
        ).order_by("name")

    def _admin_only(self, request):
        if not request.user.is_admin:
            return Response({"detail": "Справочник тегов меняет администратор"},
                            status=status.HTTP_403_FORBIDDEN)
        return None

    def create(self, request, *args, **kwargs):
        return self._admin_only(request) or super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return self._admin_only(request) or super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        denied = self._admin_only(request)
        if denied:
            return denied
        tag = self.get_object()
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
        if not request.user.is_admin:
            rows = rows.filter(employee_id__in=request.user.visible_employee_ids())
        return Response(TagMemberSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        tag = self.get_object()
        employee = get_object_or_404(Employee, id=request.data.get("employee_id"))
        if not can_assign(request.user, employee):
            return Response({"detail": "Назначать теги может руководитель — сотрудникам своей структуры"},
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
            return Response({"detail": "Снимать теги может руководитель — сотрудникам своей структуры"},
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
