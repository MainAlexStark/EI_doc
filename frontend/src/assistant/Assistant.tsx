/** Вкладка «Ассистент»: список чатов и чат на всю ширину; контекст — отдельным окном. */

import { useCallback, useEffect, useState } from "react";
import "./assistant.css";
import Icon, { type IconName } from "../ui/Icon";
import { confirmDialog, promptDialog } from "../ui/Modal";
import { Alert, PageActions, SearchField, Spinner } from "../ui/controls";
import { PHONE, useMediaQuery } from "../ui/hooks";
import {
  createConversation,
  deleteConversation,
  fetchModels,
  listConversations,
  updateConversation,
  type Conversation,
  type ModelInfo,
} from "./api";
import AgentFrame from "./AgentFrame";
import { fetchAgentLink, type AgentLink } from "./api";
import ChatView from "./ChatView";
import ContextWindow from "./ContextPanel";

const SUGGESTIONS: { title: string; text: string; icon: IconName }[] = [
  {
    icon: "search",
    title: "Найти тип СИ по неполным данным",
    text: "Нужно найти номер в Госреестре (ФИФ) для манометра. Известна только фирма-изготовитель: ",
  },
  {
    icon: "checkCircle",
    title: "Проверить поверку прибора",
    text: "Проверь, действует ли поверка прибора с заводским номером ",
  },
  {
    icon: "doc",
    title: "Составить протокол по образцу",
    text: "Составь черновик протокола по приложенному образцу. Данные объекта: ",
  },
  {
    icon: "gauge",
    title: "Завести новый тип счётчика",
    text: "Заведи в справочник новый тип счётчика воды, номер в Госреестре ",
  },
  {
    icon: "book",
    title: "Методика поверки",
    text: "Найди методику поверки и перечисли операции, средства поверки и условия для ",
  },
  {
    icon: "clock",
    title: "Эталоны: сроки поверки",
    text: "Покажи наши эталоны и у каких поверка истекает в ближайшие 2 месяца — сверь сроки с ФГИС.",
  },
];

/** Ассистент на agentkit, если он развёрнут (AGENT_URL в .env EI_doc), иначе — встроенный чат. */
export default function Assistant() {
  const [link, setLink] = useState<AgentLink | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    fetchAgentLink().then(setLink).catch(() => setFailed(true));
  }, []);
  if (link?.enabled) return <AgentFrame url={link.url} origin={link.origin} token={link.token} />;
  if (link || failed) return <BuiltinAssistant />;
  return <Spinner label="Подключаю ассистента…" />;
}

function BuiltinAssistant() {
  const phone = useMediaQuery(PHONE);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [modelsNotice, setModelsNotice] = useState("");
  const [query, setQuery] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [focusFile, setFocusFile] = useState<number | null>(null);
  const [listOpen, setListOpen] = useState(!phone);
  const [initialPrompt, setInitialPrompt] = useState<string | undefined>();
  const [draftPrompt, setDraftPrompt] = useState("");
  const [error, setError] = useState("");

  const loadList = useCallback(() => {
    listConversations(query).then(setConversations).catch((e) => setError((e as Error).message));
  }, [query]);

  useEffect(() => {
    const t = window.setTimeout(loadList, query ? 300 : 0);
    return () => window.clearTimeout(t);
  }, [loadList, query]);

  useEffect(() => {
    fetchModels()
      .then((d) => {
        setModels(d.models);
        setDefaultModel(d.default);
        setModelsNotice(d.notice || "");
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const startChat = async (prompt?: string) => {
    try {
      const conversation = await createConversation(defaultModel);
      setInitialPrompt(prompt);
      setCurrent(conversation.id);
      setDraftPrompt("");
      loadList();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (c: Conversation) => {
    if (!(await confirmDialog({ title: "Удалить чат?", text: `«${c.title}» удалится вместе с перепиской.`, danger: true }))) return;
    await deleteConversation(c.id);
    if (current === c.id) setCurrent(null);
    loadList();
  };

  const rename = async (c: Conversation) => {
    const title = await promptDialog({ title: "Название чата", value: c.title, ok: "Сохранить" });
    if (title != null && title.trim()) {
      await updateConversation(c.id, { title: title.trim() });
      loadList();
    }
  };

  const select = (id: number | null) => {
    setInitialPrompt(undefined);
    setCurrent(id);
    if (phone) setListOpen(false);
  };

  const pinned = conversations.filter((c) => c.pinned);
  const rest = conversations.filter((c) => !c.pinned);
  const currentTitle = conversations.find((c) => c.id === current)?.title;

  return (
    <div className={`assistant ${listOpen ? "" : "no-sidebar"}`}>
      <PageActions>
        <button type="button" aria-label="Контекст ассистента" title="Файлы компании, память, MCP-инструменты и правила" onClick={() => { setFocusFile(null); setContextOpen(true); }}>
          <Icon name="book" size={16} />
          <span className="hide-phone">Контекст</span>
        </button>
        <button type="button" className="primary" aria-label="Новый чат" onClick={() => select(null)}>
          <Icon name="plus" size={16} strokeWidth={2.2} />
          <span className="hide-phone">Новый чат</span>
        </button>
      </PageActions>

      {listOpen && (
        <aside className="conv-sidebar">
          <SearchField value={query} onChange={setQuery} placeholder="Поиск по чатам" />
          <div className="conv-list">
            {pinned.length > 0 && <div className="conv-group">Закреплённые</div>}
            {[...pinned, ...rest].map((c, i) => (
              <div key={c.id}>
                {i === pinned.length && pinned.length > 0 && <div className="conv-group">Остальные</div>}
                <div
                  className={`conv-item ${current === c.id ? "active" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => select(c.id)}
                  onKeyDown={(e) => e.key === "Enter" && select(c.id)}
                >
                  {c.pinned && <Icon name="pin" size={13} className="conv-pin" />}
                  <span className="conv-title">{c.title}</span>
                  <span className="conv-actions" onClick={(e) => e.stopPropagation()}>
                    <button className="icon-btn sm" title={c.pinned ? "Открепить" : "Закрепить"} aria-label={c.pinned ? "Открепить" : "Закрепить"} onClick={() => void updateConversation(c.id, { pinned: !c.pinned }).then(loadList)}>
                      <Icon name="pin" size={14} />
                    </button>
                    <button className="icon-btn sm" title="Переименовать" aria-label="Переименовать" onClick={() => void rename(c)}>
                      <Icon name="pen" size={14} />
                    </button>
                    <button className="icon-btn sm danger" title="Удалить" aria-label="Удалить" onClick={() => void remove(c)}>
                      <Icon name="trash" size={14} />
                    </button>
                  </span>
                </div>
              </div>
            ))}
            {conversations.length === 0 && <div className="conv-empty">{query ? "Ничего не нашлось" : "Чатов пока нет — начните первый справа"}</div>}
          </div>
        </aside>
      )}

      <section className="assistant-main">
        <div className="assistant-bar">
          <button className={`icon-btn ${listOpen ? "active" : ""}`} title="Список чатов" aria-label="Список чатов" onClick={() => setListOpen((o) => !o)}>
            <Icon name="sidebar" />
          </button>
          <b className="assistant-title">{currentTitle ?? "Новый чат"}</b>
        </div>
        {error && <div className="assistant-alert"><Alert onClose={() => setError("")}>{error}</Alert></div>}
        {modelsNotice && <div className="assistant-alert"><Alert tone="warn">{modelsNotice}</Alert></div>}
        {current ? (
          <ChatView
            key={current}
            conversationId={current}
            models={models}
            defaultModel={defaultModel}
            onChanged={loadList}
            onOpenContextFile={(id) => {
              setFocusFile(id);
              setContextOpen(true);
            }}
            initialPrompt={initialPrompt}
          />
        ) : (
          <div className="welcome">
            <div className="welcome-inner">
              <div className="welcome-logo"><Icon name="sparkle" size={28} strokeWidth={1.8} /></div>
              <h2>Ассистент-метролог</h2>
              <p className="muted">
                Ищет во ФГИС «Аршин», в методиках и файлах компании, составляет протоколы и заводит типы СИ — и всегда
                показывает, откуда взял каждое значение.
              </p>
              <form
                className="welcome-input"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (draftPrompt.trim()) void startChat(draftPrompt.trim());
                }}
              >
                <textarea
                  rows={3}
                  autoFocus={!phone}
                  placeholder="Опишите задачу — например, «найди тип манометра ДМ 02 и методику поверки»"
                  value={draftPrompt}
                  onChange={(e) => setDraftPrompt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (draftPrompt.trim()) void startChat(draftPrompt.trim());
                    }
                  }}
                />
                <div className="welcome-input-bar">
                  <span className="hint">Enter — начать, Shift+Enter — новая строка</span>
                  <button className="primary send" disabled={!draftPrompt.trim()} aria-label="Начать" title="Начать">
                    <Icon name="send" size={17} strokeWidth={2.2} />
                  </button>
                </div>
              </form>
              <div className="prompt-examples">
                {SUGGESTIONS.map((s) => (
                  <button key={s.title} type="button" onClick={() => setDraftPrompt(s.text)}>
                    <Icon name={s.icon} size={18} />
                    <span>
                      <b>{s.title}</b>
                      <small>{s.text}</small>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </section>

      <ContextWindow open={contextOpen} onClose={() => setContextOpen(false)} models={models} focusFileId={focusFile} />
    </div>
  );
}
