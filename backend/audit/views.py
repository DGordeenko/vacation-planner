"""Просмотр журнала изменений."""

from datetime import timedelta

from django.db import models
from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from audit import recording
from audit.labels import FIELD_LABELS
from audit.models import AuditLog
from config.params import parse_date


def _models_by_name():
    return {model.__name__: model for model in recording.TRACKED}


class AuditLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    subject_name = serializers.CharField(
        source="subject_employee.full_name", read_only=True, default=None)
    entity_type_display = serializers.SerializerMethodField()
    changes = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "entity_type", "entity_type_display", "entity_id", "entity_label",
                  "action", "action_display", "changed_by", "changed_by_name",
                  "subject_employee", "subject_name", "changed_at", "diff", "changes",
                  "source"]

    def _model(self, obj):
        return _models_by_name().get(obj.entity_type)

    def get_entity_type_display(self, obj):
        model = self._model(obj)
        return str(model._meta.verbose_name) if model else obj.entity_type

    def get_changes(self, obj):
        """Изменения для показа человеку: подпись поля, было и стало.

        Ссылки на другие объекты (руководитель, подразделение, тег) заменяются
        их названием, чтобы в журнале не стояли голые идентификаторы.
        Для создания «было» пусто, для удаления пусто «стало».
        """
        model = self._model(obj)
        diff = obj.diff or {}
        if obj.action == AuditLog.Action.CREATE:
            pairs = {k: (None, v) for k, v in (diff.get("after") or {}).items()}
        elif obj.action == AuditLog.Action.DELETE:
            pairs = {k: (v, None) for k, v in (diff.get("before") or {}).items()}
        else:
            pairs = {k: (v.get("was"), v.get("became")) for k, v in diff.items()
                     if isinstance(v, dict)}
        rows = []
        for name, (was, became) in pairs.items():
            if name == "id" or (was in (None, "") and became in (None, "")):
                continue
            field = self._field(model, name)
            rows.append({
                "field": name,
                "label": FIELD_LABELS.get(
                    name, str(field.verbose_name) if field else name),
                "was": self._display(field, was),
                "became": self._display(field, became),
            })
        return rows

    @staticmethod
    def _field(model, name):
        if model is None:
            return None
        try:
            return model._meta.get_field(name)
        except Exception:
            return None

    def _display(self, field, value):
        if value is None or field is None:
            return value
        if isinstance(field, models.ForeignKey):
            cache = self.context.setdefault("fk_cache", {})
            key = (field.related_model, value)
            if key not in cache:
                related = field.related_model.objects.filter(pk=value).first()
                cache[key] = str(related) if related else "удалён"
            return cache[key]
        if field.choices:
            return str(dict(field.flatchoices).get(value, value))
        if isinstance(value, bool):
            return "да" if value else "нет"
        return value


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
        changed_by = params.get("changed_by")
        if changed_by:
            queryset = queryset.filter(changed_by_id=changed_by)
        action_code = params.get("action")
        if action_code:
            queryset = queryset.filter(action=action_code)
        since = params.get("since")
        if since:
            queryset = queryset.filter(changed_at__gte=since)
        until = params.get("until")
        if until:
            # Дата «по» включительно: всё до начала следующего дня.
            queryset = queryset.filter(
                changed_at__lt=parse_date(until, "until") + timedelta(days=1))
        return queryset

    @action(detail=False, methods=["get"], url_path="entity-types")
    def entity_types(self, request):
        """Что пишется в журнал — для фильтра по типу объекта."""
        rows = [{"code": name, "name": str(model._meta.verbose_name)}
                for name, model in _models_by_name().items()]
        return Response(sorted(rows, key=lambda r: r["name"]))
