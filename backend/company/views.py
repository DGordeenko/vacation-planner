"""Административные эндпоинты календаря праздников."""

import base64
import binascii

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from company.importers import import_holidays
from company.models import Holiday
from config.params import parse_year
from config.permissions import ADMIN_ONLY
from schedule.models import ScheduleEntry


class HolidaySerializer(serializers.ModelSerializer):
    day_type_display = serializers.CharField(
        source="get_day_type_display", read_only=True)
    weekday = serializers.SerializerMethodField()

    class Meta:
        model = Holiday
        fields = ["id", "date", "name", "day_type", "day_type_display", "weekday"]

    def get_weekday(self, obj):
        return obj.date.weekday()


class HolidayAdminViewSet(viewsets.ModelViewSet):
    """CRUD календаря. Чтение доступно всем, изменение — администратору."""

    serializer_class = HolidaySerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "years"):
            return super().get_permissions()
        return [permission() for permission in ADMIN_ONLY]

    def get_queryset(self):
        queryset = Holiday.objects.all()
        year = self.request.query_params.get("year")
        if year:
            queryset = queryset.filter(date__year=parse_year(year))
        return queryset

    @action(detail=False, methods=["get"])
    def years(self, request):
        """Годы, на которые календарь уже заведён."""
        rows = Holiday.objects.dates("date", "year")
        return Response([{"year": row.year,
                          "count": Holiday.objects.filter(date__year=row.year).count()}
                         for row in rows])

    @action(detail=False, methods=["post"], url_path="import")
    def import_csv(self, request):
        """Загрузка календаря из CSV. dry_run=true — предпросмотр без записи."""
        content = request.data.get("content")
        if not content:
            return Response({"detail": "Не передано содержимое файла"},
                            status=status.HTTP_400_BAD_REQUEST)

        if isinstance(content, str) and request.data.get("base64"):
            try:
                raw = base64.b64decode(content)
            except (binascii.Error, ValueError):
                return Response({"detail": "Не удалось разобрать файл"},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            raw = content.encode("utf-8") if isinstance(content, str) else content

        year = request.data.get("year")
        report = import_holidays(
            raw,
            year=parse_year(year) if year else None,
            replace=bool(request.data.get("replace")),
            dry_run=bool(request.data.get("dry_run")),
        )
        payload = {
            "applied": report.applied and not request.data.get("dry_run"),
            "total_rows": report.total_rows,
            "created": report.created,
            "updated": report.updated,
            "problems": [p.__dict__ for p in report.problems],
            "warnings": [w.__dict__ for w in report.warnings],
            "preview": report.preview,
        }
        code = (status.HTTP_200_OK if report.applied
                else status.HTTP_409_CONFLICT)
        return Response(payload, status=code)

    @action(detail=False, methods=["post"], url_path="copy-year")
    def copy_year(self, request):
        """Перенос календаря на следующий год — черновик для правки вручную.

        Даты сдвигаются на тот же день и месяц; выпадение на выходные
        не пересчитывается, так как переносы устанавливаются постановлением
        и предсказать их нельзя.
        """
        source = request.data.get("source_year")
        target = request.data.get("target_year")
        if not source or not target:
            return Response({"detail": "Нужны source_year и target_year"},
                            status=status.HTTP_400_BAD_REQUEST)
        source = parse_year(source, "source_year")
        target = parse_year(target, "target_year")

        if Holiday.objects.filter(date__year=target).exists():
            return Response(
                {"detail": f"Календарь на {target} год уже заведён. "
                           f"Удалите его или правьте вручную.",
                 "code": "target_year_exists"},
                status=status.HTTP_409_CONFLICT)

        created, skipped = 0, []
        for row in Holiday.objects.filter(date__year=source):
            try:
                moved = row.date.replace(year=target)
            except ValueError:  # 29 февраля
                skipped.append(str(row.date))
                continue
            Holiday.objects.create(date=moved, name=row.name, day_type=row.day_type)
            created += 1

        return Response({"created": created, "skipped": skipped,
                         "target_year": target},
                        status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        """Удаление праздника меняет расчёт уже внесённых отпусков."""
        instance.delete()

    @action(detail=False, methods=["delete"], url_path="clear-year")
    def clear_year(self, request):
        year = request.query_params.get("year")
        if not year:
            return Response({"detail": "Не указан год"},
                            status=status.HTTP_400_BAD_REQUEST)
        deleted, _ = Holiday.objects.filter(date__year=year).delete()
        return Response({"deleted": deleted})

    @action(detail=False, methods=["get"], url_path="impact")
    def impact(self, request):
        """Сколько уже внесённых отпусков затронет правка календаря.

        Отпуска не пересчитываются автоматически (см. 05-CALCULATIONS.md),
        поэтому администратор должен видеть масштаб последствий.
        """
        year = parse_year(request.query_params.get("year"))
        entries = ScheduleEntry.objects.filter(
            record_type=ScheduleEntry.RecordType.VACATION,
            date_from__year=year,
        ).exclude(status=ScheduleEntry.Status.CANCELLED).select_related("employee")
        return Response({
            "year": year,
            "affected_entries": entries.count(),
            "affected_employees": entries.values("employee").distinct().count(),
        })
