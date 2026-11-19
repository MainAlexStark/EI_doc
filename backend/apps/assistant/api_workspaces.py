"""API режима «Создать шаблон протокола поверки» — /api/assistant/template-workspaces/…

* ``GET  /``                — мои рабочие места (последние сверху);
* ``POST /``                — {si_type} или {fif_uuid, name?}: открыть рабочее место по типу (новое — агент
  сразу получает методику и документы типа и делает первый черновик);
* ``GET  /<id>/``           — состояние: тип, документы ФИФ, черновик и действующий шаблон, занят ли агент;
* ``PATCH /<id>/``          — {family}: поправить вид СИ, если он угадался неверно;
* ``GET  /<id>/preview.<pdf|docx>`` — протокол по черновику (пустой, на пробных значениях параметров);
* ``POST /<id>/publish/``   — «Продолжить»: черновик становится действующим шаблоном типа.

Логика — ``apps.assistant.template_workspace``.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistant import template_workspace as tw
from apps.assistant.models import TemplateWorkspace
from apps.verification.api_templates import MIME, _file


def _mine(request, pk) -> TemplateWorkspace:
    return get_object_or_404(
        TemplateWorkspace.objects.select_related("si_type__family", "draft", "published", "conversation"),
        pk=pk, user=request.user,
    )


def _error(exc: Exception) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class WorkspaceListView(APIView):
    def get(self, request):
        qs = TemplateWorkspace.objects.filter(user=request.user).select_related(
            "si_type__family", "conversation")[:200]
        return Response([tw.workspace_json(w, full=False) for w in qs])

    def post(self, request):
        try:
            si_type = tw.ensure_si_type(request.user, si_type_id=request.data.get("si_type"),
                                        fif_ident=request.data.get("fif_uuid") or "",
                                        name=request.data.get("name") or "")
            workspace, created = tw.open_workspace(request.user, si_type)
        except tw.WorkspaceError as exc:
            return _error(exc)
        return Response(tw.workspace_json(workspace), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class WorkspaceDetailView(APIView):
    def get(self, request, pk: int):
        return Response(tw.workspace_json(_mine(request, pk)))

    def patch(self, request, pk: int):
        workspace = _mine(request, pk)
        if "family" in request.data:
            try:
                workspace = tw.change_family(workspace, request.user, request.data.get("family"))
            except tw.WorkspaceError as exc:
                return _error(exc)
        return Response(tw.workspace_json(_mine(request, workspace.pk)))


class WorkspacePreviewView(APIView):
    def get(self, request, pk: int, fmt: str):
        if fmt not in MIME:
            return Response({"detail": "Формат — pdf или docx"}, status=status.HTTP_400_BAD_REQUEST)
        workspace = _mine(request, pk)
        try:
            data = tw.preview(workspace, fmt)
        except tw.WorkspaceError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:  # noqa: BLE001 — нет typst, шаблон не собирается: человеку — причина
            return Response({"detail": f"Не удалось собрать {fmt}: {exc}"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        name = f"Шаблон протокола — {workspace.title or workspace.si_type.name}.{fmt}"
        response = _file(data, fmt, name)
        if request.query_params.get("inline"):
            response["Content-Disposition"] = response["Content-Disposition"].replace("attachment", "inline", 1)
        return response


class WorkspacePublishView(APIView):
    def post(self, request, pk: int):
        try:
            workspace = tw.publish(_mine(request, pk), request.user)
        except tw.WorkspaceError as exc:
            return _error(exc)
        return Response(tw.workspace_json(_mine(request, workspace.pk)))
