from celery import shared_task


@shared_task(
    name="apps.assistant.tasks.run_agent", queue="assistant",
    soft_time_limit=25 * 60, time_limit=26 * 60, acks_late=False,
)
def run_agent(run_id: int) -> None:
    # acks_late выключен: повторный прогон после падения воркера задублировал бы
    # вызовы инструментов. Прогон, оборванный падением, помечается при следующем
    # открытии чата как ошибочный (apps.assistant.api._reap_stale).
    from apps.assistant.agent import execute_run

    execute_run(run_id)



@shared_task(
    name="apps.assistant.tasks.run_template_job", queue="assistant",
    soft_time_limit=25 * 60, time_limit=26 * 60, acks_late=False,
)
def run_template_job(job_id: int) -> None:
    # acks_late выключен по той же причине: повтор задублировал бы шаблоны.
    from apps.assistant.template_jobs import run

    run(job_id)
