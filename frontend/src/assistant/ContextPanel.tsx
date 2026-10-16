/** «Контекст ассистента»: что агент знает и чем умеет пользоваться.
 *
 * Файлы компании (с режимом «всегда / поиск / выкл» и бюджетом), память,
 * инструменты и MCP-серверы, настройки. Одна панель — чтобы при сотнях
 * файлов было видно, что именно агент учтёт в ответе.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createMcp,
  createMemory,
  deleteKnowledge,
  deleteMcp,
  deleteMemory,
  fetchKnowledge,
  fetchMcpTools,
  fetchMemory,
  fetchSettings,
  fetchTools,
  fetchUsage,
  getKnowledgeFile,
  openFile,
  searchKnowledge,
  updateKnowledge,
  updateMcp,
  updateMemory,
  updateSettings,
  uploadKnowledge,
  type AssistantSettings,
  type KnowledgeFile,
  type KnowledgeListing,
  type McpServer,
  type MemoryItem,
  type ModelInfo,
} from "./api";
import ModelPicker from "./ModelPicker";

type Tab = "files" | "memory" | "tools" | "settings";

const MODES: { value: KnowledgeFile["mode"]; label: string; hint: string }[] = [
  { value: "always", label: "Всегда", hint: "Файл целиком в каждом ответе — для коротких правил" },
  { value: "search", label: "Поиск", hint: "Агент находит нужные фрагменты сам — для методик и больших документов" },
  { value: "off", label: "Выкл", hint: "Агент файл не видит" },
];

const tokens = (chars: number) => Math.round(chars / 3.5);
const fmtK = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K` : String(n));

export default function ContextPanel({
  models, focusFileId, onClose,
}: {
  models: ModelInfo[];
  focusFileId: number | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("files");
  useEffect(() => {
    if (focusFileId) setTab("files");
  }, [focusFileId]);
  return (
    <aside className="ctx-panel">
      <div className="ctx-head">
        <b>Контекст ассистента</b>
        <button className="icon" onClick={onClose} title="Скрыть">×</button>
      </div>
      <div className="ctx-tabs">
        {([
          ["files", "Файлы"],
          ["memory", "Память"],
          ["tools", "Инструменты"],
          ["settings", "Настройки"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      <div className="ctx-body">
        {tab === "files" && <FilesTab focusFileId={focusFileId} />}
        {tab === "memory" && <MemoryTab />}
        {tab === "tools" && <ToolsTab />}
        {tab === "settings" && <SettingsTab models={models} />}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
function FilesTab({ focusFileId }: { focusFileId: number | null }) {
  const [data, setData] = useState<KnowledgeListing | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [scopeFilter, setScopeFilter] = useState<"all" | "company" | "personal">("all");
  const [openId, setOpenId] = useState<number | null>(focusFileId);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [testQuery, setTestQuery] = useState("");
  const [testHits, setTestHits] = useState<Awaited<ReturnType<typeof searchKnowledge>> | null>(null);

  const load = useCallback(() => {
    fetchKnowledge().then(setData).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    if (focusFileId) setOpenId(focusFileId);
  }, [focusFileId]);

  const groups = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    const files = data.files.filter(
      (f) =>
        (scopeFilter === "all" || f.scope === scopeFilter) &&
        (!q || `${f.title} ${f.description} ${f.original_name}`.toLowerCase().includes(q)),
    );
    return data.categories
      .map((c) => ({ ...c, files: files.filter((f) => f.category === c.value) }))
      .filter((g) => g.files.length > 0);
  }, [data, query, scopeFilter]);

  const setMode = async (file: KnowledgeFile, mode: KnowledgeFile["mode"]) => {
    try {
      await updateKnowledge(file.id, { mode });
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!data) return <div className="muted pad">{error || "Загрузка…"}</div>;
  const counts = {
    always: data.files.filter((f) => f.mode === "always").length,
    search: data.files.filter((f) => f.mode === "search").length,
    off: data.files.filter((f) => f.mode === "off").length,
  };
  const budgetPct = Math.min(100, (data.always_chars / Math.max(data.always_budget, 1)) * 100);

  return (
    <div className="files-tab">
      <div className="ctx-summary">
        <div className="ctx-stat"><b>{counts.always}</b><span>всегда в контексте</span></div>
        <div className="ctx-stat"><b>{counts.search}</b><span>по поиску</span></div>
        <div className="ctx-stat"><b>{counts.off}</b><span>выключено</span></div>
      </div>
      <div className="budget" title="Файлы «Всегда» уходят в каждый запрос целиком: чем больше, тем дороже и медленнее ответ">
        <div className="budget-bar"><i style={{ width: `${budgetPct}%` }} className={budgetPct >= 100 ? "over" : ""} /></div>
        <span className="small muted">
          «Всегда»: ~{fmtK(tokens(data.always_chars))} из {fmtK(tokens(data.always_budget))} токенов
          {budgetPct >= 100 && " — бюджет превышен, лишние файлы агент будет искать поиском"}
        </span>
      </div>

      <div className="files-toolbar">
        <input placeholder="Найти файл…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select value={scopeFilter} onChange={(e) => setScopeFilter(e.target.value as typeof scopeFilter)}>
          <option value="all">Все</option>
          <option value="company">Общие</option>
          <option value="personal">Мои личные</option>
        </select>
        <button className="primary" onClick={() => setUploadOpen((o) => !o)}>+ Добавить</button>
      </div>
      {uploadOpen && (
        <UploadForm
          categories={data.categories}
          canCompany={data.can_edit_company}
          onDone={() => {
            setUploadOpen(false);
            load();
          }}
        />
      )}
      {error && <div className="chat-error">{error}</div>}

      {groups.length === 0 && (
        <div className="empty-hint">
          {data.files.length === 0 ? (
            <>
              <b>Пока ни одного файла.</b>
              <p>
                Загрузите сюда то, что агент должен учитывать: регламенты и правила компании, методики поверки, описания
                типов, свидетельства эталонов, образцы протоколов. Короткие правила — режим «Всегда», большие
                документы — «Поиск».
              </p>
            </>
          ) : (
            "Ничего не найдено"
          )}
        </div>
      )}

      {groups.map((group) => (
        <details key={group.value} className="file-group" open>
          <summary>
            {group.label} <span className="muted">· {group.files.length}</span>
          </summary>
          {group.files.map((file) => (
            <FileRow
              key={file.id}
              file={file}
              open={openId === file.id}
              onToggle={() => setOpenId((id) => (id === file.id ? null : file.id))}
              onMode={(m) => void setMode(file, m)}
              onChanged={load}
              categories={data.categories}
            />
          ))}
        </details>
      ))}

      <div className="search-test">
        <div className="small muted">Проверить, что агент найдёт по запросу:</div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (testQuery.trim()) void searchKnowledge(testQuery).then(setTestHits);
          }}
        >
          <input placeholder="например: МИ 1592 условия поверки" value={testQuery} onChange={(e) => setTestQuery(e.target.value)} />
          <button>Найти</button>
        </form>
        {testHits && (
          <ol className="hits">
            {testHits.length === 0 && <li className="muted">Ничего — агент по такому запросу файл не найдёт</li>}
            {testHits.map((h, i) => (
              <li key={i}>
                <button className="link" onClick={() => setOpenId(h.file_id)}>{h.file}</button>
                {h.section && <span className="muted"> · {h.section}</span>}
                <div className="small hit-text">{h.text}</div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function FileRow({
  file, open, onToggle, onMode, onChanged, categories,
}: {
  file: KnowledgeFile;
  open: boolean;
  onToggle: () => void;
  onMode: (m: KnowledgeFile["mode"]) => void;
  onChanged: () => void;
  categories: { value: string; label: string }[];
}) {
  const [detail, setDetail] = useState<KnowledgeFile | null>(null);
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ title: file.title, description: file.description, category: file.category });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      void getKnowledgeFile(file.id).then(setDetail);
      ref.current?.scrollIntoView({ block: "nearest" });
    }
  }, [open, file.id]);

  const save = async () => {
    await updateKnowledge(file.id, form);
    setEdit(false);
    onChanged();
  };

  return (
    <div className={`file-row ${open ? "open" : ""} mode-${file.mode}`} ref={ref}>
      <div className="file-line">
        <button className="file-title" onClick={onToggle} title={file.description || file.title}>
          <span className="file-name">{file.title}</span>
          <span className="file-meta muted small">
            {file.scope === "personal" && <span className="badge">личный</span>}
            {file.extract_error ? <span className="warn-text">⚠ без текста</span> : `~${fmtK(tokens(file.chars))} ток.`}
          </span>
        </button>
        <div className="seg" role="group">
          {MODES.map((m) => (
            <button
              key={m.value}
              className={file.mode === m.value ? "active" : ""}
              title={m.hint}
              disabled={!file.can_edit}
              onClick={() => onMode(m.value)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      {open && (
        <div className="file-detail">
          {file.extract_error && <div className="warn-text small">{file.extract_error}</div>}
          {edit ? (
            <div className="file-edit">
              <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
              <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
              <textarea
                rows={2}
                placeholder="О чём файл — агент по этому описанию решает, когда его открыть"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
              <div className="row-gap">
                <button className="primary" onClick={() => void save()}>Сохранить</button>
                <button onClick={() => setEdit(false)}>Отмена</button>
              </div>
            </div>
          ) : (
            <>
              <div className="small">{file.description || <span className="muted">Нет описания — добавьте, чтобы агент понимал, когда файл нужен</span>}</div>
              <div className="small muted">
                {file.original_name || "текстовая заметка"} · {file.chunks ?? "?"} фрагм. · {file.uploaded_by}
              </div>
            </>
          )}
          {detail?.text_preview && <pre className="file-preview">{detail.text_preview}</pre>}
          <div className="row-gap">
            {file.has_file && (
              <button className="link" onClick={() => void openFile(`/api/assistant/knowledge/${file.id}/download/`, file.original_name, true)}>
                Скачать оригинал
              </button>
            )}
            {file.can_edit && !edit && <button className="link" onClick={() => setEdit(true)}>Изменить</button>}
            {file.can_edit && (
              <button
                className="link danger"
                onClick={() => {
                  if (window.confirm(`Удалить «${file.title}» из контекста?`)) void deleteKnowledge(file.id).then(onChanged);
                }}
              >
                Удалить
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function UploadForm({
  categories, canCompany, onDone,
}: {
  categories: { value: string; label: string }[];
  canCompany: boolean;
  onDone: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [category, setCategory] = useState("methods");
  const [mode, setMode] = useState("search");
  const [scope, setScope] = useState(canCompany ? "company" : "personal");
  const [description, setDescription] = useState("");
  const [noteTitle, setNoteTitle] = useState("");
  const [noteText, setNoteText] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  const submit = async () => {
    setBusy(true);
    const lines: string[] = [];
    try {
      if (noteText.trim()) {
        await uploadKnowledge(null, { title: noteTitle || "Заметка", category, mode, scope, description, text: noteText });
        lines.push(`✓ ${noteTitle || "Заметка"}`);
      }
      for (const file of files) {
        try {
          const kf = await uploadKnowledge(file, { category, mode, scope, description });
          lines.push(kf.extract_error ? `⚠ ${file.name}: ${kf.extract_error}` : `✓ ${file.name}`);
        } catch (e) {
          lines.push(`✕ ${file.name}: ${(e as Error).message}`);
        }
        setLog([...lines]);
      }
      setLog(lines);
      if (lines.every((l) => l.startsWith("✓"))) onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="upload-form">
      <label
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          setFiles((f) => [...f, ...Array.from(e.dataTransfer.files)]);
        }}
      >
        <input
          type="file"
          multiple
          hidden
          onChange={(e) => {
            const picked = e.target.files ? Array.from(e.target.files) : [];
            setFiles((f) => [...f, ...picked]);
            e.target.value = "";
          }}
        />
        {files.length ? files.map((f) => f.name).join(", ") : "Перетащите файлы сюда или нажмите — PDF, Word, Excel, TXT"}
      </label>
      <details>
        <summary className="small">…или текстовая заметка (правило, памятка)</summary>
        <input placeholder="Название" value={noteTitle} onChange={(e) => setNoteTitle(e.target.value)} />
        <textarea rows={4} placeholder="Текст" value={noteText} onChange={(e) => setNoteText(e.target.value)} />
      </details>
      <div className="grid2">
        <label className="field">
          <span>Раздел</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Режим</span>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            {MODES.map((m) => <option key={m.value} value={m.value}>{m.label} — {m.hint}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Кому</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            {canCompany && <option value="company">Всей компании</option>}
            <option value="personal">Только мне</option>
          </select>
        </label>
        <label className="field">
          <span>О чём (необязательно)</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </div>
      <div className="row-gap">
        <button className="primary" disabled={busy || (!files.length && !noteText.trim())} onClick={() => void submit()}>
          {busy ? "Загружаю и индексирую…" : "Загрузить"}
        </button>
        <button onClick={onDone}>Отмена</button>
      </div>
      {log.length > 0 && <ul className="small">{log.map((l, i) => <li key={i}>{l}</li>)}</ul>}
    </div>
  );
}

// ---------------------------------------------------------------------------
function MemoryTab() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [canCompany, setCanCompany] = useState(false);
  const [draft, setDraft] = useState("");
  const [scope, setScope] = useState("personal");
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchMemory()
      .then((d) => {
        setItems(d.items);
        setCanCompany(d.can_edit_company);
      })
      .catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);

  const section = (title: string, list: MemoryItem[], hint: string) => (
    <div className="mem-section">
      <div className="mem-title">{title} <span className="muted">· {list.length}</span></div>
      {list.length === 0 && <div className="muted small">{hint}</div>}
      {list.map((m) =>
        editing?.id === m.id ? (
          <div key={m.id} className="mem-item">
            <textarea rows={2} value={editing.text} onChange={(e) => setEditing({ id: m.id, text: e.target.value })} />
            <div className="row-gap">
              <button className="primary" onClick={() => void updateMemory(m.id, editing.text).then(() => { setEditing(null); load(); })}>Сохранить</button>
              <button onClick={() => setEditing(null)}>Отмена</button>
            </div>
          </div>
        ) : (
          <div key={m.id} className="mem-item">
            <span>{m.text}</span>
            {m.can_edit && (
              <span className="mem-actions">
                <button className="link" onClick={() => setEditing({ id: m.id, text: m.text })}>✎</button>
                <button className="link danger" onClick={() => void deleteMemory(m.id).then(load)}>×</button>
              </span>
            )}
          </div>
        ),
      )}
    </div>
  );

  return (
    <div className="memory-tab">
      <p className="small muted">
        Память — короткие факты, которые агент учитывает в каждом чате. Агент сам предлагает, что запомнить; общие
        правила компании сохраняются только после подтверждения метролога.
      </p>
      {error && <div className="chat-error">{error}</div>}
      {section("Общее для компании", items.filter((m) => m.scope === "company"), "Например: «Протоколы по манометрам оформляем в серии 01»")}
      {section("Только для меня", items.filter((m) => m.scope === "personal"), "Например: «Мои районы — Слободской и Кирово-Чепецк»")}
      <div className="mem-add">
        <textarea rows={2} placeholder="Новый факт…" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <div className="row-gap">
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="personal">Только мне</option>
            {canCompany && <option value="company">Всей компании</option>}
          </select>
          <button
            className="primary"
            disabled={!draft.trim()}
            onClick={() => void createMemory(draft, scope).then(() => { setDraft(""); load(); }).catch((e) => setError((e as Error).message))}
          >
            Запомнить
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function ToolsTab() {
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchTools>> | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", title: "", url: "", token: "", description: "" });
  const [error, setError] = useState("");
  const load = useCallback(() => {
    fetchTools().then(setData).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(load, [load]);
  if (!data) return <div className="muted pad">{error || "Загрузка…"}</div>;

  return (
    <div className="tools-tab">
      <div className="mem-title">MCP-серверы</div>
      {data.servers.length === 0 && (
        <div className="muted small">
          Нет подключённых серверов. RSTMetrology_MCP подключается сам, если в .env задан RST_MCP_URL — или добавьте его здесь.
        </div>
      )}
      {data.servers.map((s) => <McpRow key={s.id} server={s} canEdit={data.can_edit} onChanged={load} />)}
      {data.can_edit && !adding && <button onClick={() => setAdding(true)}>+ Подключить MCP-сервер</button>}
      {adding && (
        <div className="upload-form">
          <div className="grid2">
            <label className="field"><span>Имя (латиница)</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="rst" /></label>
            <label className="field"><span>Название</span><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
          </div>
          <label className="field"><span>Адрес /mcp</span><input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="http://rstmetrology-mcp:8080/mcp" /></label>
          <label className="field"><span>Токен (если нужен)</span><input type="password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} /></label>
          <label className="field"><span>Когда использовать</span><input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <div className="row-gap">
            <button className="primary" onClick={() => void createMcp(form).then(() => { setAdding(false); load(); }).catch((e) => setError((e as Error).message))}>Подключить</button>
            <button onClick={() => setAdding(false)}>Отмена</button>
          </div>
        </div>
      )}
      {error && <div className="chat-error">{error}</div>}

      <div className="mem-title">Встроенные инструменты</div>
      {!data.web_search && (
        <div className="warn-text small">Веб-поиск выключен: нужен ключ Yandex Search API (YANDEX_SEARCH_API_KEY или YANDEX_VISION_API_KEY + каталог).</div>
      )}
      <ul className="tool-list">
        {data.builtin.map((t) => (
          <li key={t.name} title={t.description}>
            <b>{t.label}</b> <code className="muted">{t.name}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

function McpRow({ server, canEdit, onChanged }: { server: McpServer; canEdit: boolean; onChanged: () => void }) {
  const [state, setState] = useState<Awaited<ReturnType<typeof fetchMcpTools>> | null>(null);
  const [open, setOpen] = useState(false);
  const check = (refresh = false) => {
    setState(null);
    void fetchMcpTools(server.id, refresh).then(setState);
  };
  useEffect(() => check(), [server.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleTool = async (name: string, enabled: boolean) => {
    const disabled = enabled ? server.disabled_tools.filter((t) => t !== name) : [...server.disabled_tools, name];
    await updateMcp(server.id, { disabled_tools: disabled });
    onChanged();
    check();
  };

  return (
    <div className={`mcp-row ${server.enabled ? "" : "off"}`}>
      <div className="file-line">
        <button className="file-title" onClick={() => setOpen((o) => !o)}>
          <span className="file-name">
            {state === null ? <i className="spinner" /> : state.ok ? "🟢" : "🔴"} {server.title || server.name}
          </span>
          <span className="muted small">
            {state?.ok ? `${state.tools.filter((t) => t.enabled).length} инструм.` : state?.error ?? "проверяю…"}
          </span>
        </button>
        {canEdit && (
          <label className="switch small">
            <input type="checkbox" checked={server.enabled} onChange={(e) => void updateMcp(server.id, { enabled: e.target.checked }).then(onChanged)} />
            включён
          </label>
        )}
      </div>
      {open && (
        <div className="file-detail">
          <div className="small muted">{server.url} {server.has_auth && "· с токеном"}</div>
          {server.description && <div className="small">{server.description}</div>}
          {state?.tools.map((t) => (
            <label key={t.name} className="tool-toggle small" title={t.description}>
              <input type="checkbox" disabled={!canEdit} checked={t.enabled} onChange={(e) => void toggleTool(t.name, e.target.checked)} />
              <b>{t.title || t.name}</b> <span className="muted">{t.description.slice(0, 120)}</span>
            </label>
          ))}
          <div className="row-gap">
            <button className="link" onClick={() => check(true)}>Проверить связь</button>
            {canEdit && (
              <button className="link danger" onClick={() => window.confirm("Отключить сервер?") && void deleteMcp(server.id).then(onChanged)}>
                Удалить
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function SettingsTab({ models }: { models: ModelInfo[] }) {
  const [s, setS] = useState<AssistantSettings | null>(null);
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof fetchUsage>> | null>(null);
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    fetchSettings().then(setS).catch((e) => setError((e as Error).message));
    fetchUsage().then(setUsage).catch(() => undefined);
  }, []);
  if (!s) return <div className="muted pad">{error || "Загрузка…"}</div>;
  const save = async () => {
    try {
      setS(await updateSettings(s));
      setSaved("Сохранено");
      setTimeout(() => setSaved(""), 2000);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="settings-tab">
      <label className="field">
        <span>Модель по умолчанию для новых чатов</span>
        {s.can_edit ? <ModelPicker models={models} value={s.default_model} onChange={(id) => setS({ ...s, default_model: id })} /> : <div>{s.default_model}</div>}
      </label>
      <label className="field">
        <span>Правила компании для ассистента</span>
        <textarea
          rows={9}
          disabled={!s.can_edit}
          placeholder={"Добавляются к системному промту в каждом чате. Например:\n— Протоколы по счётчикам воды — по МИ 1592-2015.\n— Эталоны берём только из справочника EI_doc.\n— Если клиент — юрлицо, указывать ИНН."}
          value={s.extra_instructions}
          onChange={(e) => setS({ ...s, extra_instructions: e.target.value })}
        />
      </label>
      <div className="grid2">
        <label className="field">
          <span>Шагов агента на ответ</span>
          <input type="number" min={3} max={80} disabled={!s.can_edit} value={s.max_steps} onChange={(e) => setS({ ...s, max_steps: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>Бюджет «Всегда», символов</span>
          <input type="number" step={10000} disabled={!s.can_edit} value={s.always_budget_chars} onChange={(e) => setS({ ...s, always_budget_chars: Number(e.target.value) })} />
        </label>
      </div>
      {s.can_edit && (
        <div className="row-gap">
          <button className="primary" onClick={() => void save()}>Сохранить</button>
          <span className="muted small">{saved}</span>
        </div>
      )}
      {error && <div className="chat-error">{error}</div>}
      {usage && usage.rows.length > 0 && (
        <>
          <div className="mem-title">Расход за 30 дней</div>
          <table className="w-table">
            <thead><tr><th>Модель</th><th>Ответов</th><th>Токенов</th><th>$</th></tr></thead>
            <tbody>
              {usage.rows.map((r) => (
                <tr key={r.model}><td>{r.model.replace(/^openrouter:/, "")}</td><td>{r.calls}</td><td>{fmtK(r.tokens)}</td><td>{r.cost.toFixed(2)}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
