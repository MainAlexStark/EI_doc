"""Список сотрудников — для выпадающих списков «назначить исполнителя»

на экранах заявок, нарядов и задач. Отдельный файл, а не часть views.py:
это API, не диагностика.
"""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Employee


class EmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    tab_number = serializers.CharField()
    position = serializers.CharField()


class EmployeeListView(APIView):
    """GET /api/core/employees/ — только действующие, для выбора исполнителя."""

    def get(self, request):
        qs = Employee.objects.filter(is_active=True).order_by("full_name")
        return Response(EmployeeSerializer(qs, many=True).data)
