"""Просмотр журнала изменений."""

from django.db.models import Q
from rest_framework import serializers, viewsets

from audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    subject_name = serializers.CharField(
        source="subject_employee.full_name", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "entity_type", "entity_id", "entity_label",
                  "action", "action_display", "changed_by", "changed_by_name",
                  "subject_employee", "subject_name", "changed_at", "diff", "source"]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Журнал только для чтения.

    Изменение и удаление записей не предусмотрены намеренно: журнал,
    который можно править, не является доказательством.
    """

    serializer_class = AuditLogSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = AuditLog.objects.all().select_related(
            "changed_by", "subject_employee")

        # Администратор видит всё; руководитель — изменения по своим людям
        # и свои собственные действия; рядовой сотрудник — только по себе.
        if not user.is_admin:
            visible = user.visible_employee_ids()
            queryset = queryset.filter(
                Q(subject_employee_id__in=visible) | Q(changed_by=user))

        params = self.request.query_params
        entity_type = params.get("entity_type")
        if entity_type:
            queryset = queryset.filter(entity_type=entity_type)
        entity_id = params.get("entity_id")
        if entity_id:
            queryset = queryset.filter(entity_id=entity_id)
        employee_id = params.get("employee_id")
        if employee_id:
            queryset = queryset.filter(subject_employee_id=employee_id)
        action = params.get("action")
        if action:
            queryset = queryset.filter(action=action)
        since = params.get("since")
        if since:
            queryset = queryset.filter(changed_at__gte=since)
        return queryset
