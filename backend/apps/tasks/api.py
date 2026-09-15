"""API задач: список/CRUD, доска, календарь, загрузка сотрудников на неделю.

Обычный CRUD выражен через DRF generics + ModelSerializer — задача не
считает ничего похожего на нумерацию или измерения, писать его вручную,
как в apps.verification, смысла нет. Представления (доска/календарь/
загрузка) — свои вьюхи поверх той же модели.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tasks.models import Task, TaskStatus


class TaskSerializer(serializers.ModelSerializer):
    assignee_name = serializers.CharField(source="assignee.full_name", default="", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", default="", read_only=True)
    progress = serializers.IntegerField(read_only=True)
    children_count = serializers.IntegerField(source="children.count", read_only=True)
    linked_label = serializers.SerializerMethodField()
    content_type = serializers.SlugRelatedField(
        slug_field="model", queryset=ContentType.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Task
        fields = [
            "id", "title", "description", "parent", "assignee", "assignee_name",
            "created_by", "created_by_name", "status", "priority", "due_date",
            "estimated_hours", "content_type", "object_id", "linked_label",
            "recurrence", "recurrence_parent", "progress", "children_count",
            "created_at", "updated_at", "completed_at",
        ]
        read_only_fields = ["created_at", "updated_at", "completed_at"]

    def get_linked_label(self, obj: Task) -> str:
        return str(obj.linked_object) if obj.linked_object else ""


class TaskListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/tasks/

    ?mine=1 — задачи текущего пользователя (по core.Employee, не по User);
    остальные фильтры — обычные query-параметры django-filter.
    """

    serializer_class = TaskSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["status", "priority", "assignee", "parent", "content_type", "object_id"]
    search_fields = ["title", "description"]

    def get_queryset(self):
        qs = Task.objects.select_related("assignee", "created_by", "content_type").prefetch_related("children")
        if self.request.query_params.get("mine"):
            employee = getattr(self.request.user, "employee", None)
            qs = qs.filter(assignee=employee) if employee else qs.none()
        due_before = self.request.query_params.get("due_before")
        if due_before:
            qs = qs.filter(due_date__lte=due_before)
        return qs

    def perform_create(self, serializer):
        employee = getattr(self.request.user, "employee", None)
        serializer.save(created_by=employee)


class TaskDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Task.objects.select_related("assignee", "created_by", "content_type")
    serializer_class = TaskSerializer


class TaskBoardView(APIView):
    """GET /api/tasks/board/?assignee= — задачи, сгруппированные по статусу."""

    def get(self, request):
        qs = Task.objects.select_related("assignee").filter(status__in=[
            TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.DONE
        ])
        assignee = request.query_params.get("assignee")
        if assignee:
            qs = qs.filter(assignee_id=assignee)

        columns: dict[str, list] = {choice.value: [] for choice in TaskStatus if choice != TaskStatus.CANCELLED}
        for task in qs:
            columns.setdefault(task.status, []).append(TaskSerializer(task).data)
        return Response(columns)


class TaskCalendarView(APIView):
    """GET /api/tasks/calendar/?date_from=&date_to="""

    def get(self, request):
        date_from = request.query_params.get("date_from") or timezone.localdate().isoformat()
        date_to = request.query_params.get("date_to") or (
            timezone.localdate() + dt.timedelta(days=30)
        ).isoformat()
        qs = (
            Task.objects.select_related("assignee")
            .filter(due_date__gte=date_from, due_date__lte=date_to)
            .exclude(status=TaskStatus.CANCELLED)
            .order_by("due_date")
        )
        by_day: dict[str, list] = defaultdict(list)
        for task in qs:
            by_day[task.due_date.isoformat()].append(TaskSerializer(task).data)
        return Response(by_day)


class TaskWorkloadView(APIView):
    """GET /api/tasks/workload/?date_from=&date_to= — загрузка сотрудников за период.

    Период обычно неделя — решает фронтенд, здесь просто сумма по нему,
    без встроенной разбивки на недели: конторе это не нужно на её объёмах.
    """

    def get(self, request):
        today = timezone.localdate()
        date_from = request.query_params.get("date_from") or (today - dt.timedelta(days=today.weekday())).isoformat()
        date_to = request.query_params.get("date_to") or (
            today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=6)
        ).isoformat()

        qs = (
            Task.objects.select_related("assignee")
            .filter(due_date__gte=date_from, due_date__lte=date_to)
            .exclude(status=TaskStatus.CANCELLED)
            .exclude(assignee__isnull=True)
        )

        totals: dict[int, dict] = {}
        for task in qs:
            bucket = totals.setdefault(
                task.assignee_id,
                {"employee_id": task.assignee_id, "employee": task.assignee.full_name, "hours": 0.0, "count": 0},
            )
            bucket["hours"] += float(task.estimated_hours or 0)
            bucket["count"] += 1

        return Response({
            "date_from": date_from,
            "date_to": date_to,
            "employees": sorted(totals.values(), key=lambda item: -item["hours"]),
        })
