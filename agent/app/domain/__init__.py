"""Ассистент-метролог EI_doc на agentkit.

Предметной части здесь почти нет, и так задумано: инструменты (журнал, типы СИ,
шаблоны поверки и аттестации, личный кабинет, файлы компании, документы) и всё,
что зависит от пользователя и компании (правила руководства, общая память, файлы
«Всегда» и оглавление файлов «Поиск», кто пользователь и его роль), отдаёт сам
EI_doc — MCP-сервером хоста (``ei`` в mcp.json, ``per_user``) с правами этого
сотрудника. ФГИС «Аршин» — сервер ``rst`` (RSTMetrology_MCP). Здесь — промпт
предметной области и пустой экран чата.
"""

from __future__ import annotations

from pathlib import Path

from agentkit import DEFAULT_SUBAGENTS, AgentSpec
from agentkit.server.service import Domain
from agentkit.server.settings import Settings

from app.domain.hooks import hooks
from app.domain.tools import TOOLS

HERE = Path(__file__).parent


def build_domain(settings: Settings) -> Domain:
    return Domain(
        spec=AgentSpec(
            name="main",
            prompt=(HERE / "prompt.md").read_text(encoding="utf-8"),
            model="main",
        ),
        tools=TOOLS,
        hooks=hooks,
        subagents=dict(DEFAULT_SUBAGENTS),
        description="Ищет во ФГИС «Аршин», в методиках и файлах компании, заводит типы СИ, делает шаблоны "
                    "протоколов поверки и аттестации, ведёт ваш график, наряды и задачи в EI_doc.",
        suggestions=[
            "Нужно найти номер в Госреестре (ФИФ) для манометра. Известна только фирма-изготовитель: ",
            "Проверь, действует ли поверка прибора с заводским номером ",
            "Сделай шаблон протокола поверки по приложенному образцу",
            "Покажи наши эталоны и у каких поверка истекает в ближайшие 2 месяца — сверь сроки с ФГИС.",
            "Что у меня завтра?",
        ],
    )
