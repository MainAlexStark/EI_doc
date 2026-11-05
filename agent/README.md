# Ассистент EI_doc на agentkit

Вкладка «Ассистент» EI_doc — веб-чат этого сервиса во фрейме. Ядро (цикл, модели, права,
карточки подтверждения, память, файлы, SSE, субагенты) — `vendor/agentkit`; здесь только
предметный промпт (`app/domain/prompt.md`) и подключения (`mcp.json`):

- **`ei`** — сам EI_doc (`backend/apps/assistant/host.py`), MCP-сервер хоста `per_user`: инструменты
  ассистента (журнал, типы СИ, шаблоны поверки и аттестации, личный кабинет, файлы компании, документы)
  и инструкции на каждого сотрудника — правила руководства, общая память, файлы «Всегда» и оглавление
  файлов «Поиск», кто он и его роль. Всё выполняется в EI_doc от имени сотрудника и с его правами.
- **`rst`** — ФГИС «Аршин» (RSTMetrology_MCP).

Изменения данных компании (`*_propose`) идут через карточку: её строки собирает EI_doc «на пробу»
(`agentkit/preview` → `agentkit/dryRun`), после «Подтвердить» тот же вызов выполняется по-настоящему.
Вход — токеном от EI_doc (`AGENT_HOST_LOGIN_SECRET` = `AGENT_LOGIN_SECRET` у EI_doc), чат открывается
только во фрейме сайтов из `AGENT_EMBED_ORIGINS`.

## Развернуть (на том же VPS, что и EI_doc)

```bash
cd agent && ./deploy.sh agent.example.ru     # секреты и ключи возьмёт из ../.env EI_doc
cd .. && ./deploy.sh update                  # EI_doc узнает AGENT_URL — вкладка откроет этот чат
docker compose exec web python manage.py agent_migrate_memory   # личная память старого ассистента
```

## Локально

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest pytest-asyncio
cp deploy/ei-agent.env.example .env   # ключ модели, EI_MCP_TOKEN = AGENT_HOST_TOKEN из .env EI_doc
uvicorn app.main:app --reload --port 8080
pytest && agentkit eval evals/ --app app.evals:make_agent --offline
```

Обновить ядро: `cd ~/alex/agentkit && git pull`, затем здесь `agentkit vendor && pytest`.
