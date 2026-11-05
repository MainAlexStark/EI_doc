"""Свои инструменты агента — пусто намеренно.

Все предметные инструменты отдаёт EI_doc (backend/apps/assistant/host.py) MCP-сервером
хоста: так они работают с данными и правами EI_doc, а не с их копией. Новый инструмент
ассистента добавляется там — в apps/assistant/tools.py или cabinet.py; здесь — только то,
что не ходит в данные EI_doc.
"""

from __future__ import annotations

TOOLS: list = []
