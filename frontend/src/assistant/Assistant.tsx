/** Вкладка «Ассистент»: список чатов, чат, панель контекста — по образцу OpenRouter Chat. */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import "./assistant.css";
import {
  createConversation,
  deleteConversation,
  fetchModels,
  listConversations,
  updateConversation,
  type Conversation,
  type ModelInfo,
} from "./api";
import ChatView from "./ChatView";
import ContextPanel from "./ContextPanel";

const SUGGESTIONS = [
  {
    title: "Найти тип СИ по неполным данным",
    text: "Нужно найти номер в Госреестре (ФИФ) для манометра. Известна только фирма-изготовитель: ",
  },
  {
    title: "Проверить поверку прибора",
    text: "Проверь, действует ли поверка прибора с заводским номером ",
  },
  {
    title: "Составить протокол по образцу",
    text: "Составь черновик протокола по приложенному образцу. Данные объекта: ",
  },
  {
    title: "Завести новый тип счётчика",
    text: "Заведи в справочник новый тип счётчика воды, номер в Госреестре ",
  },
  {
    title: "Методика поверки",
    text: "Найди методику поверки и перечисли операции, средства поверки и условия для ",
  },
  {
    title: "Эталоны: сроки поверки",
    text: "Покажи наши эталоны и у каких поверка истекает в ближайшие 2 месяца — сверь сроки с ФГИС.",
  },
];

const PANEL_KEY = "ei_doc_assistant_context_open";

export default function Assistant() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [query, setQuery] = useState("");
  const [panelOpen, setPanelOpen] = useState(() => {
    try {
      return localStorage.getItem(PANEL_KEY) !== "0";
    } catch {
      return true;
    }
  });
  const [focusFile, setFocusFile] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [initialPrompt, setInitialPrompt] = useState<string | undefined>();
  const [draftPrompt, setDraftPrompt] = useState("");
  const [error, setError] = useState("");
  // Шапка приложения на телефоне переносится в несколько строк — высоту чата
  // считаем от фактического верха блока, а не от фиксированной высоты шапки.
  const rootRef = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(57);
  useLayoutEffect(() => {
    const measure = () => setTop(Math.max(0, Math.round(rootRef.current?.getBoundingClientRect().top ?? 57) + window.scrollY));
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

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
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(PANEL_KEY, panelOpen ? "1" : "0");
    } catch {
      /* ок */
    }
  }, [panelOpen]);

  const startChat = async (prompt?: string) => {
    try {
      const conversation = await createConversation(defaultModel);
      setInitialPrompt(prompt);
      setCurrent(conversation.id);
      loadList();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (id: number) => {
    if (!window.confirm("Удалить чат?")) return;
    await deleteConversation(id);
    if (current === id) setCurrent(null);
    loadList();
  };

  const rename = async (c: Conversation) => {
    const title = window.prompt("Название чата", c.title);
    if (title != null) {
      await updateConversation(c.id, { title });
      loadList();
    }
  };

  const pinned = conversations.filter((c) => c.pinned);
  const rest = conversations.filter((c) => !c.pinned);

  return (
    <div
      ref={rootRef}
      className={`assistant ${panelOpen ? "with-panel" : ""} ${sidebarOpen ? "" : "no-sidebar"}`}
      style={{ ["--top" as string]: `${top}px` }}
    >
      {sidebarOpen && (
        <aside className="conv-sidebar">
          <button className="primary new-chat" onClick={() => setCurrent(null)}>+ Новый чат</button>
          <input className="conv-search" placeholder="Поиск по чатам…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <div className="conv-list">
            {pinned.length > 0 && <div className="conv-group">Закреплённые</div>}
            {[...pinned, ...rest].map((c, i) => (
              <div key={c.id}>
                {i === pinned.length && pinned.length > 0 && <div className="conv-group">Остальные</div>}
                <div className={`conv-item ${current === c.id ? "active" : ""}`} onClick={() => { setInitialPrompt(undefined); setCurrent(c.id); }}>
                  <span className="conv-title">{c.title}</span>
                  <span className="conv-actions" onClick={(e) => e.stopPropagation()}>
                    <button className="link" title={c.pinned ? "Открепить" : "Закрепить"} onClick={() => void updateConversation(c.id, { pinned: !c.pinned }).then(loadList)}>
                      {c.pinned ? "★" : "☆"}
                    </button>
                    <button className="link" title="Переименовать" onClick={() => void rename(c)}>✎</button>
                    <button className="link danger" title="Удалить" onClick={() => void remove(c.id)}>×</button>
                  </span>
                </div>
              </div>
            ))}
            {conversations.length === 0 && <div className="muted small pad">Чатов пока нет</div>}
          </div>
        </aside>
      )}

      <section className="assistant-main">
        <div className="assistant-bar">
          <button className="icon" title="Список чатов" onClick={() => setSidebarOpen((o) => !o)}>☰</button>
          <b className="assistant-title">{conversations.find((c) => c.id === current)?.title ?? "Ассистент-метролог"}</b>
          <div className="spacer" />
          <button className={panelOpen ? "active-toggle" : ""} onClick={() => setPanelOpen((o) => !o)}>
            📚 Контекст
          </button>
        </div>
        {error && <div className="chat-error bar">{error}</div>}
        {current ? (
          <ChatView
            key={current}
            conversationId={current}
            models={models}
            defaultModel={defaultModel}
            onChanged={loadList}
            onOpenContextFile={(id) => {
              setPanelOpen(true);
              setFocusFile(id);
            }}
            initialPrompt={initialPrompt}
          />
        ) : (
          <div className="welcome">
            <div className="welcome-inner">
              <div className="welcome-logo">Σ</div>
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
                  autoFocus
                  placeholder="Опишите задачу…"
                  value={draftPrompt}
                  onChange={(e) => setDraftPrompt(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (draftPrompt.trim()) void startChat(draftPrompt.trim());
                    }
                  }}
                />
                <button className="primary" disabled={!draftPrompt.trim()}>Начать</button>
              </form>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s.title} onClick={() => setDraftPrompt(s.text)}>
                    <b>{s.title}</b>
                    <span>{s.text}</span>
                  </button>
                ))}
              </div>
              {models.length === 0 && (
                <div className="warn-text small">
                  Модели не настроены: администратору нужно задать OPENROUTER_API_KEY (и/или ключи Yandex AI Studio) в .env.
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      {panelOpen && <ContextPanel models={models} focusFileId={focusFile} onClose={() => setPanelOpen(false)} />}
    </div>
  );
}
