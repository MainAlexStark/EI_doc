"""Новый тип СИ из Госреестра → агент в фоне готовит черновик шаблона поверки."""

from __future__ import annotations

import copy
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.assistant import host, template_jobs
from apps.assistant.models import Conversation, TemplateJob
from apps.catalog.models import MeasurementFamily, SiType, StandardKit
from apps.core.models import Role, User
from apps.verification import templates
from apps.verification.models import VerificationTemplate
from apps.verification.tests.test_templates import MANOMETER

AGENT = {"AGENT_INTERNAL_URL": "http://agent.test", "AGENT_SERVICE_TOKEN": "svc"}


class FakeAgent:
    """Вместо сервиса агента: «агент» читает задачу и сохраняет шаблон тем же инструментом хоста."""

    def __init__(self, save: dict | None):
        self.save = save
        self.calls = []

    def __call__(self, url, json, headers, timeout):
        self.calls.append({"url": url, "text": json["text"], "headers": headers})
        user = User.objects.get(pk=int(headers["X-Agent-User-Id"]))
        job_id = int(json["text"].split("job_id=")[1].split(")")[0])
        if self.save is not None:
            conv = Conversation.objects.create(user=user)
            result = host.call(user, conv, "ei_template_job_save", {"job_id": job_id, **self.save})
            assert not result["isError"], result
        response = mock.Mock(status_code=200)
        response.json.return_value = {"session_id": "s-1", "status": "done", "text": "Шаблон по МИ 2124-90."}
        return response


@override_settings(**AGENT)
class TemplateJobTests(TestCase):
    def setUp(self):
        self.family = MeasurementFamily.objects.create(code="manometers", name="Манометры")
        self.water = MeasurementFamily.objects.create(code="water", name="Счётчики воды", calculator_key="water_meter")
        self.kit = StandardKit.objects.create(section="Единый перечень для поверки манометров", scope="до 2,5 МПа")
        self.kit.families.set([self.family])
        self.verifier = User.objects.create_user("v@ei.test", "pw", role=Role.VERIFIER)
        self.metrologist = User.objects.create_user("m@ei.test", "pw", role=Role.METROLOGIST)
        self.save = {"kind": "verification", "title": "ДМ — МИ 2124-90", "plan": copy.deepcopy(MANOMETER),
                     "kits": [self.kit.pk], "sources": ["МИ 2124-90, пп. 5.1–5.4"]}

    def create_type(self, user, agent, **over):
        client = APIClient()
        client.force_authenticate(user)
        body = {"registry_number": "39153-08", "name": "ДМ 02", "fif_uuid": "uuid-1",
                "type_title": "Манометры показывающие", **over}
        with mock.patch("apps.assistant.template_jobs.requests.post", agent):
            with self.captureOnCommitCallbacks(execute=True):
                response = client.post(reverse("si_type_list"), body, format="json")
        assert response.status_code == 201, response.data
        return SiType.objects.get(pk=response.data["id"])

    def test_type_from_registry_gets_draft_template_with_kits(self):
        agent = FakeAgent(self.save)
        si_type = self.create_type(self.metrologist, agent)
        assert si_type.family == self.family  # угадано по наименованию типа из реестра
        job = TemplateJob.objects.get(si_type=si_type)
        assert job.status == TemplateJob.Status.DONE, job.error
        template = job.verification_template
        assert template.si_type == si_type and list(template.kits.all()) == [self.kit]
        # Черновик агента ждёт проверки, даже если тип заводил метролог.
        assert template.needs_review and template.created_via_assistant
        assert agent.calls[0]["url"] == "http://agent.test/api/run"
        assert "Перечень применяемых эталонов" in agent.calls[0]["text"]
        assert templates.template_for(si_type) == template
        data = APIClient()
        data.force_authenticate(self.verifier)
        body = data.get(reverse("si_type_list")).data[0]
        assert body["template_job"]["status"] == "done" and body["templates"][0]["kits"]

    def test_agent_did_not_save_is_failure(self):
        si_type = self.create_type(self.verifier, FakeAgent(None))
        job = TemplateJob.objects.get(si_type=si_type)
        assert job.status == TemplateJob.Status.FAILED and "не сохранил" in job.error

    def test_redo_updates_same_draft(self):
        si_type = self.create_type(self.verifier, FakeAgent(self.save))
        first = TemplateJob.objects.get(si_type=si_type).verification_template
        client = APIClient()
        client.force_authenticate(self.verifier)
        redo = FakeAgent({**self.save, "note": "добавил точку 25 %"})
        with mock.patch("apps.assistant.template_jobs.requests.post", redo):
            with self.captureOnCommitCallbacks(execute=True):
                r = client.post(reverse("si_type_template_jobs", args=[si_type.pk]), {"comment": "нужна точка 25 %"},
                                format="json")
        assert r.status_code == 201
        assert "нужна точка 25 %" in redo.calls[0]["text"]
        assert VerificationTemplate.objects.count() == 1
        first.refresh_from_db()
        assert first.note == "добавил точку 25 %"
        assert TemplateJob.objects.filter(si_type=si_type).first().status == TemplateJob.Status.DONE

    def test_existing_family_template_is_reused_without_losing_review(self):
        shared = templates.save_template(self.metrologist, {"title": "Манометры — общий", "family": self.family.pk,
                                                            "plan": copy.deepcopy(MANOMETER)}, sources=["МИ"])
        assert not shared.needs_review
        agent = FakeAgent({"kind": "verification", "template_id": shared.pk, "kits": [self.kit.pk],
                           "sources": ["шаблон семейства подходит"]})
        si_type = self.create_type(self.verifier, agent)
        assert f"«{shared.title}» (id {shared.pk})" in agent.calls[0]["text"]
        shared.refresh_from_db()
        assert not shared.needs_review and list(shared.kits.all()) == [self.kit]
        assert TemplateJob.objects.get(si_type=si_type).status == TemplateJob.Status.DONE

    def test_water_meters_need_no_template(self):
        agent = FakeAgent(self.save)
        si_type = self.create_type(self.verifier, agent, family=self.water.pk, limits={
            "q_min": 0.03, "q_transition_a": 0.15, "q_transition_b": 0.12, "q_nominal": 1.5, "q_max": 3,
            "error_below_transition": 5, "error_above_transition": 2})
        assert TemplateJob.objects.get(si_type=si_type).status == TemplateJob.Status.SKIPPED
        assert agent.calls == []

    def test_save_tool_only_for_running_job_of_same_user(self):
        si_type = SiType.objects.create(family=self.family, registry_number="1-01", name="X", fif_uuid="u")
        job = TemplateJob.objects.create(si_type=si_type, requested_by=self.verifier,
                                         status=TemplateJob.Status.RUNNING)
        conv = Conversation.objects.create(user=self.metrologist)
        r = host.call(self.metrologist, conv, "ei_template_job_save", {"job_id": job.pk, **self.save})
        assert r["isError"] and "фоновой работы" in r["content"][0]["text"]
        spec = host.tool_spec("ei_template_job_save", host.registry(self.verifier)["ei_template_job_save"])
        assert spec["annotations"]["readOnlyHint"] is True  # без карточки: черновик проверит метролог

    @override_settings(AGENT_SERVICE_TOKEN="")
    def test_without_agent_job_fails_with_reason(self):
        si_type = self.create_type(self.verifier, FakeAgent(self.save))
        job = TemplateJob.objects.get(si_type=si_type)
        assert job.status == TemplateJob.Status.FAILED and "не подключён" in job.error
        assert template_jobs.job_dict(job)["status_display"] == "не получилось"
