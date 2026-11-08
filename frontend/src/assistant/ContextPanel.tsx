/** «Контекст ассистента» — отдельное окно (как «Настройки» в macOS): слева разделы, справа содержимое.
 *
 * Файлы компании (режим «всегда / поиск / выкл» и бюджет), память, инструменты и MCP-серверы,
 * правила и расход. Раньше это была узкая панель справа от чата — при сотнях файлов и десятке
 * MCP-инструментов в ней было тесно; теперь у каждого раздела вся ширина окна.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Icon, { type IconName } from "../ui/Icon";
import { confirmDialog, Modal } from "../ui/Modal";
import { Alert, EmptyState, SearchField, Segmented, Spinner, Switch } from "../ui/controls";
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

export type ContextSection = "files" | "memory" | "tools" | "rules" | "usage";

const MODES: { value: KnowledgeFile["mode"]; label: string; hint: string }[] = [
  { value: "always", label: "Всегда", hint: "Файл целиком в каждом ответе — для коротких правил" },
  { value: "search", label: "Поиск", hint: "Агент находит нужные фрагменты сам — для методик и больших документов" },
  { value: "off", label: "Выкл", hint: "Агент файл не видит" },
];

const tokens = (chars: number) => Math.round(chars / 3.5);
const fmtK = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K` : String(n));
const errText = (e: unknown) => (e instanceof Error ? e.message : "Ошибка");

export default function ContextWindow({
  open, onClose, models, focusFileId, agentMode = false, initial = "files",
}: {
  open: boolean;
  onClose: () => void;
  models: ModelInfo[];
  focusFileId: number | null;
  /** Чат — агент на agentkit: модели, MCP-серверы и личная память живут в его настройках,
   *  здесь — только то, что EI_doc отдаёт агенту сам (файлы, общая память, правила). */
  agentMode?: boolean;
  initial?: ContextSection;
}) {
  const [section, setSection] = useState<ContextSection>(initial);
  useEffect(() => {
    if (focusFileId) setSection("files");
  }, [focusFileId]);
  useEffect(() => {
    if (open) setSection(focusFileId ? "files" : initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const nav: { key: ContextSection; label: string; icon: IconName; hint: string }[] = [
    { key: "files", label: "Файлы компании", icon: "folder", hint: "Регламенты, методики, образцы" },
    { key: "memory", label: agentMode ? "Общая память" : "Память", icon: "memory", hint: "Короткие факты для каждого чата" },
    { key: "tools", label: "Инструменты и MCP", icon: "plug", hint: "ФГИС «Аршин», EI_doc, веб-поиск" },
    { key: "rules", label: agentMode ? "Правила компании" : "Правила и модель", icon: "book", hint: "Добавка к системному промту" },
    ...(agentMode ? [] : [{ key: "usage" as ContextSection, label: "Расход", icon: "chart" as IconName, hint: "Ответы и токены за 30 дней" }]),
  ];
  const current = nav.find((n) => n.key === section) ?? nav[0];

  return (
    <Modal open={open} onClose={onClose} size="xl" bare className="ctx-window" title="Контекст ассистента" subtitle="Что ассистент знает о компании и чем умеет пользоваться">
      <nav className="ctx-nav" aria-label="Разделы контекста">
        {nav.map((item) => (
          <button
            type="button"
            key={item.key}
            className={`ctx-nav-item ${section === item.key ? "active" : ""}`}
            aria-current={section === item.key ? "page" : undefined}
            onClick={() => setSection(item.key)}
          >
            <span className="ctx-nav-icon"><Icon name={item.icon} size={16} /></span>
            <span className="ctx-nav-text">
              <b>{item.label}</b>
              <small>{item.hint}</small>
            </span>
          </button>
        ))}
      </nav>
      <section className="ctx-main" aria-label={current.label}>
        {section === "files" && <FilesSection focusFileId={focusFileId} />}
        {section === "memory" && <MemorySection agentMode={agentMode} />}
        {section === "tools" && (agentMode ? <AgentToolsSection /> : <ToolsSection />)}
        {section === "rules" && <RulesSection models={models} agentMode={agentMode} />}
        {section === "usage" && !agentMode && <UsageSection />}
      </section>
    </Modal>
  );
}

function SectionHead({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="ctx-head">
      <div className="grow">
        <h3>{title}</h3>
        {children && <p>{children}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </header>
  );
}

// ---------------------------------------------------------------------------
// Файлы

function FilesSection({ focusFileId }: { focusFileId: number | null }) {
  const [data, setData] = useState<KnowledgeListing | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [scopeFilter, setScopeFilter] = useState<"all" | "company" | "personal">("all");
  const [openId, setOpenId] = useState<number | null>(focusFileId);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [testQuery, setTestQuery] = useState("");
  const [testBusy, setTestBusy] = useState(false);
  const [testHits, setTestHits] = useState<Awaited<ReturnType<typeof searchKnowledge>> | null>(null);

  const load = useCallback(() => {
    fetchKnowledge().then(setData).catch((e) => setError(errText(e)));
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
    setData((d) => (d ? { ...d, files: d.files.map((f) => (f.id === file.id ? { ...f, mode } : f)) } : d));
    try {
      await updateKnowledge(file.id, { mode });
      load();
    } catch (e) {
      setError(errText(e));
      load();
    }
  };

  const runTest = async () => {
    if (!testQuery.trim()) return;
    setTestBusy(true);
    try {
      setTestHits(await searchKnowledge(testQuery));
    } catch (e) {
      setError(errText(e));
    } finally {
      setTestBusy(false);
    }
  };

  if (!data) return error ? <div className="ctx-body"><Alert>{error}</Alert></div> : <Spinner />;
  const counts = {
    always: data.files.filter((f) => f.mode === "always").length,
    search: data.files.filter((f) => f.mode === "search").length,
    off: data.files.filter((f) => f.mode === "off").length,
  };
  const budgetPct = Math.min(100, (data.always_chars / Math.max(data.always_budget, 1)) * 100);

  return (
    <>
      <SectionHead
        title="Файлы компании"
        actions={
          <button type="button" className="primary" onClick={() => setUploadOpen((o) => !o)}>
            <Icon name={uploadOpen ? "x" : "upload"} size={16} />
            {uploadOpen ? "Закрыть" : "Загрузить"}
          </button>
        }
      >
        Короткие правила держите в режиме «Всегда» — они уходят в каждый ответ целиком. Методики и большие документы — в
        «Поиске»: ассистент найдёт нужные фрагменты сам.
      </SectionHead>
      <div className="ctx-body">
        {uploadOpen && (
          <UploadForm
            categories={data.categories}
            canCompany={data.can_edit_company}
            onDone={() => {
              setUploadOpen(false);
              load();
            }}
            onCancel={() => setUploadOpen(false)}
          />
        )}

        <div className="ctx-stats">
          <div className="ctx-stat"><span className="ctx-stat-dot always" /><b>{counts.always}</b><span>всегда в контексте</span></div>
          <div className="ctx-stat"><span className="ctx-stat-dot search" /><b>{counts.search}</b><span>по поиску</span></div>
          <div className="ctx-stat"><span className="ctx-stat-dot off" /><b>{counts.off}</b><span>выключено</span></div>
          <div className="ctx-budget" title="Файлы «Всегда» уходят в каждый запрос целиком: чем больше, тем дороже и медленнее ответ">
            <div className="ctx-budget-top">
              <span>Бюджет «Всегда»</span>
              <b>~{fmtK(tokens(data.always_chars))} / {fmtK(tokens(data.always_budget))} ток.</b>
            </div>
            <div className="bar-wrap"><div className={`bar ${budgetPct >= 100 ? "over" : budgetPct > 80 ? "high" : ""}`} style={{ width: `${budgetPct}%` }} /></div>
            {budgetPct >= 100 && <small className="warn-text">Бюджет превышен — лишние файлы ассистент будет искать поиском</small>}
          </div>
        </div>

        <div className="toolbar" style={{ marginBottom: 12 }}>
          <SearchField className="grow" value={query} onChange={setQuery} placeholder="Найти файл" />
          <Segmented
            size="sm"
            value={scopeFilter}
            onChange={setScopeFilter}
            options={[
              { value: "all", label: "Все" },
              { value: "company", label: "Общие" },
              { value: "personal", label: "Мои" },
            ]}
          />
        </div>
        {error && <Alert onClose={() => setError("")}>{error}</Alert>}

        {groups.length === 0 &&
          (data.files.length === 0 ? (
            <EmptyState
              icon="folder"
              title="Пока ни одного файла"
              action={!uploadOpen && <button type="button" className="primary" onClick={() => setUploadOpen(true)}><Icon name="upload" size={16} /> Загрузить первый</button>}
            >
              Загрузите то, что ассистент должен учитывать: регламенты и правила компании, методики поверки, описания типов,
              свидетельства эталонов, образцы протоколов.
            </EmptyState>
          ) : (
            <EmptyState icon="search" title="Ничего не найдено" />
          ))}

        {groups.map((group) => (
          <div key={group.value} className="ctx-group">
            <div className="list-section">
              {group.label} <span className="count">{group.files.length}</span>
            </div>
            <div className="ctx-list">
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
            </div>
          </div>
        ))}

        {data.files.length > 0 && (
          <div className="ctx-card">
            <h4><Icon name="search" size={15} /> Проверить поиск</h4>
            <p className="hint">Что ассистент найдёт в файлах по такому запросу — если пусто, файл стоит описать подробнее.</p>
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                void runTest();
              }}
            >
              <input className="grow" placeholder="Например: МИ 1592 условия поверки" value={testQuery} onChange={(e) => setTestQuery(e.target.value)} />
              <button type="submit" disabled={!testQuery.trim() || testBusy}>{testBusy ? "Ищу…" : "Найти"}</button>
            </form>
            {testHits && (
              <ol className="hits">
                {testHits.length === 0 && <li className="muted">Ничего — по такому запросу ассистент файл не найдёт</li>}
                {testHits.map((h, i) => (
                  <li key={i}>
                    <button type="button" className="link" onClick={() => setOpenId(h.file_id)}>{h.file}</button>
                    {h.section && <span className="muted"> · {h.section}</span>}
                    <div className="hit-text">{h.text}</div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>
    </>
  );
}

const extOf = (file: KnowledgeFile) => (file.original_name ? file.original_name.split(".").pop()?.toLowerCase() ?? "" : "txt");

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
  const ext = extOf(file);

  useEffect(() => {
    if (open) {
      void getKnowledgeFile(file.id).then(setDetail).catch(() => undefined);
      ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [open, file.id]);

  const save = async () => {
    await updateKnowledge(file.id, form);
    setEdit(false);
    onChanged();
  };

  const remove = async () => {
    if (await confirmDialog({ title: `Удалить «${file.title}»?`, text: "Ассистент перестанет учитывать этот файл.", danger: true })) {
      await deleteKnowledge(file.id);
      onChanged();
    }
  };

  return (
    <div className={`ctx-row ${open ? "open" : ""} mode-${file.mode}`} ref={ref}>
      <div className="ctx-row-line">
        <button type="button" className="ctx-row-main" onClick={onToggle} aria-expanded={open}>
          <span className={`att-ext ext-${ext}`}>{ext.toUpperCase().slice(0, 4) || "TXT"}</span>
          <span className="ctx-row-text">
            <b>{file.title}</b>
            <small>
              {file.scope === "personal" && <span className="tag">личный</span>}
              {file.extract_error ? (
                <span className="warn-text"><Icon name="alert" size={12} /> текст не извлечён</span>
              ) : (
                <span>~{fmtK(tokens(file.chars))} ток.</span>
              )}
              <span className="ctx-row-desc">{file.description || file.original_name || "текстовая заметка"}</span>
            </small>
          </span>
        </button>
        <div className="segmented sm mode-seg" role="group" aria-label="Режим">
          {MODES.map((m) => (
            <button
              type="button"
              key={m.value}
              className={`${file.mode === m.value ? "active" : ""} m-${m.value}`}
              title={m.hint}
              disabled={!file.can_edit}
              onClick={() => onMode(m.value)}
            >
              {m.label}
            </button>
          ))}
        </div>
        <button type="button" className="icon-btn sm" onClick={onToggle} aria-label={open ? "Свернуть" : "Подробнее"}>
          <Icon name={open ? "chevronDown" : "chevronRight"} size={16} />
        </button>
      </div>
      {open && (
        <div className="ctx-row-detail">
          {file.extract_error && <Alert tone="warn">{file.extract_error}</Alert>}
          {edit ? (
            <div className="form-stack" style={{ gap: 10 }}>
              <div className="form-grid" style={{ marginBottom: 0 }}>
                <div className="field">
                  <label>Название</label>
                  <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
                </div>
                <div className="field">
                  <label>Раздел</label>
                  <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                    {categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                </div>
              </div>
              <div className="field">
                <label>О чём файл</label>
                <textarea
                  rows={2}
                  placeholder="Ассистент по этому описанию решает, когда открыть файл"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                />
              </div>
              <div className="row">
                <button type="button" className="primary sm" onClick={() => void save()}>Сохранить</button>
                <button type="button" className="sm" onClick={() => setEdit(false)}>Отмена</button>
              </div>
            </div>
          ) : (
            <>
              <div>{file.description || <span className="muted">Нет описания — добавьте, чтобы ассистент понимал, когда файл нужен</span>}</div>
              <div className="hint">
                {file.original_name || "текстовая заметка"} · {file.chunks ?? "?"} фрагм. · загрузил {file.uploaded_by} ·{" "}
                {new Date(file.updated_at).toLocaleDateString("ru-RU")}
              </div>
            </>
          )}
          {detail?.text_preview && <pre className="file-preview">{detail.text_preview}</pre>}
          {!edit && (
            <div className="row wrap">
              {file.has_file && (
                <button type="button" className="sm" onClick={() => void openFile(`/api/assistant/knowledge/${file.id}/download/`, file.original_name, true)}>
                  <Icon name="download" size={14} /> Скачать оригинал
                </button>
              )}
              {file.can_edit && (
                <button type="button" className="sm" onClick={() => setEdit(true)}>
                  <Icon name="pen" size={14} /> Изменить
                </button>
              )}
              {file.can_edit && (
                <button type="button" className="sm ghost danger" onClick={() => void remove()}>
                  <Icon name="trash" size={14} /> Удалить
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function UploadForm({
  categories, canCompany, onDone, onCancel,
}: {
  categories: { value: string; label: string }[];
  canCompany: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [asNote, setAsNote] = useState(false);
  const [category, setCategory] = useState(categories.find((c) => c.value === "methods")?.value ?? categories[0]?.value ?? "");
  const [mode, setMode] = useState<KnowledgeFile["mode"]>("search");
  const [scope, setScope] = useState(canCompany ? "company" : "personal");
  const [description, setDescription] = useState("");
  const [noteTitle, setNoteTitle] = useState("");
  const [noteText, setNoteText] = useState("");
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [log, setLog] = useState<{ ok: boolean; text: string }[]>([]);

  const submit = async () => {
    setBusy(true);
    const lines: { ok: boolean; text: string }[] = [];
    try {
      if (asNote && noteText.trim()) {
        await uploadKnowledge(null, { title: noteTitle || "Заметка", category, mode, scope, description, text: noteText });
        lines.push({ ok: true, text: noteTitle || "Заметка" });
      }
      if (!asNote) {
        for (const file of files) {
          try {
            const kf = await uploadKnowledge(file, { category, mode, scope, description });
            lines.push(kf.extract_error ? { ok: false, text: `${file.name}: ${kf.extract_error}` } : { ok: true, text: file.name });
          } catch (e) {
            lines.push({ ok: false, text: `${file.name}: ${errText(e)}` });
          }
          setLog([...lines]);
        }
      }
      setLog(lines);
      if (lines.every((l) => l.ok)) onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ctx-card upload">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h4 style={{ margin: 0 }}>Добавить в контекст</h4>
        <Segmented
          size="sm"
          value={asNote ? "note" : "file"}
          onChange={(v) => setAsNote(v === "note")}
          options={[
            { value: "file", label: "Файлы" },
            { value: "note", label: "Заметка" },
          ]}
        />
      </div>
      {asNote ? (
        <div className="form-stack" style={{ gap: 10 }}>
          <input placeholder="Название — например, «Как оформляем протоколы по манометрам»" value={noteTitle} onChange={(e) => setNoteTitle(e.target.value)} />
          <textarea rows={5} placeholder="Текст правила или памятки" value={noteText} onChange={(e) => setNoteText(e.target.value)} />
        </div>
      ) : (
        <label
          className={`dropzone ${drag ? "drag" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
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
          <Icon name="upload" size={22} />
          {files.length ? (
            <span className="dropzone-files">{files.map((f) => f.name).join(", ")}</span>
          ) : (
            <>
              <b>Перетащите файлы сюда или нажмите, чтобы выбрать</b>
              <span>PDF, Word, Excel, TXT</span>
            </>
          )}
        </label>
      )}
      <div className="form-grid" style={{ marginBottom: 0 }}>
        <div className="field">
          <label>Раздел</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {categories.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Кому</label>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            {canCompany && <option value="company">Всей компании</option>}
            <option value="personal">Только мне</option>
          </select>
        </div>
        <div className="field span-all">
          <label>Режим</label>
          <Segmented value={mode} onChange={setMode} options={MODES.map((m) => ({ value: m.value, label: m.label }))} />
          <span className="hint">{MODES.find((m) => m.value === mode)?.hint}</span>
        </div>
        <div className="field span-all">
          <label>О чём <span className="opt">— необязательно, но помогает ассистенту</span></label>
          <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Например: методика поверки счётчиков воды, разделы 6–8" />
        </div>
      </div>
      {log.length > 0 && (
        <ul className="upload-log">
          {log.map((l, i) => (
            <li key={i} className={l.ok ? "ok-text" : "warn-text"}>
              <Icon name={l.ok ? "check" : "alert"} size={14} /> {l.text}
            </li>
          ))}
        </ul>
      )}
      <div className="row">
        <button type="button" className="primary" disabled={busy || (asNote ? !noteText.trim() : !files.length)} onClick={() => void submit()}>
          {busy ? "Загружаю и индексирую…" : "Загрузить"}
        </button>
        <button type="button" onClick={onCancel}>Отмена</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Память

function MemorySection({ agentMode }: { agentMode: boolean }) {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [canCompany, setCanCompany] = useState(false);
  const [draft, setDraft] = useState("");
  const [scope, setScope] = useState(agentMode ? "company" : "personal");
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchMemory()
      .then((d) => {
        setItems(d.items);
        setCanCompany(d.can_edit_company);
      })
      .catch((e) => setError(errText(e)));
  }, []);
  useEffect(load, [load]);

  const add = () =>
    void createMemory(draft, scope)
      .then(() => {
        setDraft("");
        load();
      })
      .catch((e) => setError(errText(e)));

  const section = (title: string, list: MemoryItem[], hint: string) => (
    <div className="ctx-group">
      <div className="list-section">{title} <span className="count">{list.length}</span></div>
      {list.length === 0 ? (
        <div className="ctx-empty-line">{hint}</div>
      ) : (
        <div className="ctx-list">
          {list.map((m) =>
            editing?.id === m.id ? (
              <div key={m.id} className="ctx-mem editing">
                <textarea rows={2} value={editing.text} onChange={(e) => setEditing({ id: m.id, text: e.target.value })} autoFocus />
                <div className="row">
                  <button type="button" className="primary sm" onClick={() => void updateMemory(m.id, editing.text).then(() => { setEditing(null); load(); })}>Сохранить</button>
                  <button type="button" className="sm" onClick={() => setEditing(null)}>Отмена</button>
                </div>
              </div>
            ) : (
              <div key={m.id} className="ctx-mem">
                <Icon name="memory" size={15} />
                <span className="grow">{m.text}</span>
                {m.can_edit && (
                  <span className="ctx-mem-actions">
                    <button type="button" className="icon-btn sm" title="Изменить" aria-label="Изменить" onClick={() => setEditing({ id: m.id, text: m.text })}>
                      <Icon name="pen" size={14} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn sm danger"
                      title="Удалить"
                      aria-label="Удалить"
                      onClick={() => void confirmDialog({ title: "Забыть этот факт?", text: m.text, ok: "Забыть", danger: true }).then((ok) => { if (ok) void deleteMemory(m.id).then(load); })}
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </span>
                )}
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );

  const canAdd = !agentMode || canCompany;

  return (
    <>
      <SectionHead title={agentMode ? "Общая память" : "Память"}>
        {agentMode
          ? "Короткие факты, которые ассистент учитывает в каждом чате у каждого сотрудника. Меняет метролог или руководитель. Личная память — в меню чата: Настройки → Память."
          : "Короткие факты, которые ассистент учитывает в каждом чате. Ассистент сам предлагает, что запомнить; общие правила компании сохраняются только после подтверждения метролога."}
      </SectionHead>
      <div className="ctx-body">
        {error && <Alert onClose={() => setError("")}>{error}</Alert>}
        {canAdd && (
          <form
            className="ctx-card mem-add"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim()) add();
            }}
          >
            <textarea
              rows={2}
              placeholder={agentMode ? "Новый факт для всех — например, «Протоколы по манометрам оформляем в серии 01»" : "Новый факт…"}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <div className="row">
              {!agentMode && (
                <Segmented
                  size="sm"
                  value={scope}
                  onChange={setScope}
                  options={[
                    { value: "personal", label: "Только мне" },
                    ...(canCompany ? [{ value: "company", label: "Всей компании" }] : []),
                  ]}
                />
              )}
              <span className="grow" />
              <button type="submit" className="primary" disabled={!draft.trim()}>Запомнить</button>
            </div>
          </form>
        )}
        {items === null ? (
          <Spinner />
        ) : (
          <>
            {section("Общее для компании", items.filter((m) => m.scope === "company"), "Пока пусто. Например: «Протоколы по манометрам оформляем в серии 01».")}
            {!agentMode && section("Только для меня", items.filter((m) => m.scope === "personal"), "Пока пусто. Например: «Мои районы — Слободской и Кирово-Чепецк».")}
          </>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Инструменты и MCP

function AgentToolsSection() {
  return (
    <>
      <SectionHead title="Инструменты и MCP">
        Ассистент работает через подключения (MCP). Список подключений, модели и права настраиваются в самом чате: меню
        профиля внизу слева → «Настройки» → «Инструменты».
      </SectionHead>
      <div className="ctx-body">
        <div className="ctx-list">
          <div className="mcp-card">
            <span className="mcp-icon"><Icon name="server" size={18} /></span>
            <div className="grow">
              <b>EI_doc</b>
              <small>Журнал, типы СИ, шаблоны поверки и аттестации, график, наряды, задачи, файлы компании — с вашими правами</small>
            </div>
            <span className="pill ok">встроено</span>
          </div>
          <div className="mcp-card">
            <span className="mcp-icon"><Icon name="globe" size={18} /></span>
            <div className="grow">
              <b>ФГИС «Аршин»</b>
              <small>Поиск типов СИ, поверок, документов и характеристик в Госреестре (RSTMetrology MCP)</small>
            </div>
            <span className="pill ok">встроено</span>
          </div>
        </div>
        <Alert tone="info">
          Изменения данных компании (новый тип СИ, наряд, задача) ассистент не делает молча: в чате появляется карточка с
          подробностями и кнопкой «Подтвердить».
        </Alert>
      </div>
    </>
  );
}

function ToolsSection() {
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchTools>> | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", title: "", url: "", token: "", description: "" });
  const [error, setError] = useState("");
  const load = useCallback(() => {
    fetchTools().then(setData).catch((e) => setError(errText(e)));
  }, []);
  useEffect(load, [load]);

  const connect = () =>
    void createMcp(form)
      .then(() => {
        setAdding(false);
        setForm({ name: "", title: "", url: "", token: "", description: "" });
        load();
      })
      .catch((e) => setError(errText(e)));

  if (!data) return error ? <div className="ctx-body"><Alert>{error}</Alert></div> : <Spinner />;

  return (
    <>
      <SectionHead
        title="Инструменты и MCP"
        actions={data.can_edit && !adding && (
          <button type="button" className="primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={16} /> Подключить сервер
          </button>
        )}
      >
        MCP-серверы дают ассистенту доступ к внешним системам — например, к ФГИС «Аршин». У каждого сервера можно
        отключить отдельные инструменты.
      </SectionHead>
      <div className="ctx-body">
        {error && <Alert onClose={() => setError("")}>{error}</Alert>}
        {adding && (
          <form
            className="ctx-card"
            onSubmit={(e) => {
              e.preventDefault();
              connect();
            }}
          >
            <h4>Новый MCP-сервер</h4>
            <div className="form-grid" style={{ marginBottom: 0 }}>
              <div className="field"><label>Имя (латиница)</label><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="rst" autoFocus /></div>
              <div className="field"><label>Название</label><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="ФГИС «Аршин»" /></div>
              <div className="field span-all"><label>Адрес /mcp</label><input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="http://rstmetrology-mcp:8080/mcp" /></div>
              <div className="field"><label>Токен <span className="opt">— если нужен</span></label><input type="password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} /></div>
              <div className="field"><label>Когда использовать</label><input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Поиск в Госреестре" /></div>
            </div>
            <div className="row">
              <button type="submit" className="primary" disabled={!form.name.trim() || !form.url.trim()}>Подключить</button>
              <button type="button" onClick={() => setAdding(false)}>Отмена</button>
            </div>
          </form>
        )}

        <div className="list-section">MCP-серверы <span className="count">{data.servers.length}</span></div>
        {data.servers.length === 0 ? (
          <div className="ctx-empty-line">
            Нет подключённых серверов. RSTMetrology MCP подключается сам, если в .env задан RST_MCP_URL, — или добавьте его кнопкой выше.
          </div>
        ) : (
          <div className="ctx-list">
            {data.servers.map((s) => <McpRow key={s.id} server={s} canEdit={data.can_edit} onChanged={load} />)}
          </div>
        )}

        <div className="list-section">Встроенные инструменты <span className="count">{data.builtin.length}</span></div>
        {!data.web_search && (
          <Alert tone="warn">Веб-поиск выключен: нужен ключ Yandex Search API (YANDEX_SEARCH_API_KEY или YANDEX_VISION_API_KEY + каталог).</Alert>
        )}
        <div className="tool-grid">
          {data.builtin.map((t) => (
            <div key={t.name} className="tool-chip" title={t.description}>
              <b>{t.label}</b>
              <code>{t.name}</code>
              <small>{t.description}</small>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function McpRow({ server, canEdit, onChanged }: { server: McpServer; canEdit: boolean; onChanged: () => void }) {
  const [state, setState] = useState<Awaited<ReturnType<typeof fetchMcpTools>> | null>(null);
  const [open, setOpen] = useState(false);
  const check = (refresh = false) => {
    setState(null);
    void fetchMcpTools(server.id, refresh)
      .then(setState)
      .catch((e) => setState({ ok: false, error: errText(e), tools: [] }));
  };
  useEffect(() => check(), [server.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleTool = async (name: string, enabled: boolean) => {
    const disabled = enabled ? server.disabled_tools.filter((t) => t !== name) : [...server.disabled_tools, name];
    await updateMcp(server.id, { disabled_tools: disabled });
    onChanged();
    check();
  };

  const status = state === null ? "checking" : state.ok ? "ok" : "fail";
  const enabledCount = state?.tools.filter((t) => t.enabled).length ?? 0;

  return (
    <div className={`ctx-row mcp ${server.enabled ? "" : "off"} ${open ? "open" : ""}`}>
      <div className="ctx-row-line">
        <button type="button" className="ctx-row-main" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <span className={`mcp-status ${status}`}>{status === "checking" ? <span className="spinner sm" /> : <Icon name="server" size={16} />}</span>
          <span className="ctx-row-text">
            <b>{server.title || server.name}</b>
            <small>
              {status === "ok" && <span className="ok-text">на связи · {enabledCount} из {state!.tools.length} инструм.</span>}
              {status === "fail" && <span className="crit-text">нет связи</span>}
              {status === "checking" && <span>проверяю связь…</span>}
              <span className="ctx-row-desc">{server.url}</span>
            </small>
          </span>
        </button>
        {canEdit && (
          <input
            type="checkbox"
            role="switch"
            className="switch"
            title={server.enabled ? "Выключить сервер" : "Включить сервер"}
            checked={server.enabled}
            onChange={(e) => void updateMcp(server.id, { enabled: e.target.checked }).then(onChanged)}
          />
        )}
        <button type="button" className="icon-btn sm" onClick={() => setOpen((o) => !o)} aria-label={open ? "Свернуть" : "Подробнее"}>
          <Icon name={open ? "chevronDown" : "chevronRight"} size={16} />
        </button>
      </div>
      {open && (
        <div className="ctx-row-detail">
          {status === "fail" && state?.error && <Alert>{state.error}</Alert>}
          {server.description && <div>{server.description}</div>}
          <div className="hint">{server.url}{server.has_auth && " · с токеном"}</div>
          {state?.tools && state.tools.length > 0 && (
            <div className="tool-toggles">
              {state.tools.map((t) => (
                <Switch
                  key={t.name}
                  checked={t.enabled}
                  disabled={!canEdit}
                  onChange={(v) => void toggleTool(t.name, v)}
                  label={<span><b>{t.title || t.name}</b> <code className="muted">{t.name}</code></span>}
                  hint={t.description.length > 160 ? `${t.description.slice(0, 160)}…` : t.description}
                />
              ))}
            </div>
          )}
          <div className="row wrap">
            <button type="button" className="sm" onClick={() => check(true)}>
              <Icon name="refresh" size={14} /> Проверить связь
            </button>
            {canEdit && (
              <button
                type="button"
                className="sm ghost danger"
                onClick={() => void confirmDialog({ title: `Отключить «${server.title || server.name}»?`, text: "Ассистент перестанет пользоваться его инструментами.", ok: "Отключить", danger: true }).then((ok) => { if (ok) void deleteMcp(server.id).then(onChanged); })}
              >
                <Icon name="trash" size={14} /> Удалить
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Правила и модель

function RulesSection({ models, agentMode }: { models: ModelInfo[]; agentMode: boolean }) {
  const [s, setS] = useState<AssistantSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    fetchSettings().then(setS).catch((e) => setError(errText(e)));
  }, []);
  if (!s) return error ? <div className="ctx-body"><Alert>{error}</Alert></div> : <Spinner />;
  const save = async () => {
    setBusy(true);
    try {
      setS(await updateSettings(s));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <SectionHead
        title={agentMode ? "Правила компании" : "Правила и модель"}
        actions={s.can_edit && (
          <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
            {saved ? <><Icon name="check" size={16} /> Сохранено</> : busy ? "Сохраняю…" : "Сохранить"}
          </button>
        )}
      >
        Добавляются к системному промту в каждом чате у каждого сотрудника.
        {!s.can_edit && " Меняют метролог и руководитель."}
      </SectionHead>
      <div className="ctx-body">
        {error && <Alert onClose={() => setError("")}>{error}</Alert>}
        <div className="field">
          <label htmlFor="ctx-rules">Правила для ассистента</label>
          <textarea
            id="ctx-rules"
            className="rules-text"
            rows={12}
            disabled={!s.can_edit}
            placeholder={"Например:\n— Протоколы по счётчикам воды — по МИ 1592-2015.\n— Эталоны берём только из справочника EI_doc.\n— Если клиент — юрлицо, указывать ИНН."}
            value={s.extra_instructions}
            onChange={(e) => setS({ ...s, extra_instructions: e.target.value })}
          />
        </div>
        <div className="form-grid" style={{ marginTop: 16 }}>
          {!agentMode && (
            <div className="field span-all">
              <label>Модель по умолчанию для новых чатов</label>
              {s.can_edit ? <ModelPicker models={models} value={s.default_model} onChange={(id) => setS({ ...s, default_model: id })} /> : <div>{s.default_model}</div>}
            </div>
          )}
          {!agentMode && (
            <div className="field">
              <label>Шагов агента на ответ</label>
              <input type="number" min={3} max={80} disabled={!s.can_edit} value={s.max_steps} onChange={(e) => setS({ ...s, max_steps: Number(e.target.value) })} />
            </div>
          )}
          <div className="field">
            <label>Бюджет «Всегда», символов</label>
            <input type="number" step={10000} disabled={!s.can_edit} value={s.always_budget_chars} onChange={(e) => setS({ ...s, always_budget_chars: Number(e.target.value) })} />
            <span className="hint">≈ {fmtK(tokens(s.always_budget_chars))} токенов в каждом запросе</span>
          </div>
        </div>
        {agentMode && <Alert tone="info">Модели, лимиты и расход — в настройках самого чата (меню профиля внизу слева).</Alert>}
      </div>
    </>
  );
}

function UsageSection() {
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof fetchUsage>> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetchUsage().then(setUsage).catch((e) => setError(errText(e)));
  }, []);
  const total = usage?.rows.reduce((acc, r) => ({ calls: acc.calls + r.calls, tokens: acc.tokens + r.tokens, cost: acc.cost + r.cost }), { calls: 0, tokens: 0, cost: 0 });
  return (
    <>
      <SectionHead title="Расход">За последние 30 дней, по моделям.</SectionHead>
      <div className="ctx-body">
        {error && <Alert>{error}</Alert>}
        {!usage && !error && <Spinner />}
        {usage && usage.rows.length === 0 && <EmptyState icon="chart" title="Расхода пока нет" />}
        {usage && usage.rows.length > 0 && total && (
          <>
            <div className="ctx-stats">
              <div className="ctx-stat"><b>{total.calls}</b><span>ответов</span></div>
              <div className="ctx-stat"><b>{fmtK(total.tokens)}</b><span>токенов</span></div>
              <div className="ctx-stat"><b>${total.cost.toFixed(2)}</b><span>стоимость</span></div>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Модель</th><th className="right">Ответов</th><th className="right">Токенов</th><th className="right">$</th></tr></thead>
                <tbody>
                  {usage.rows.map((r) => (
                    <tr key={r.model}>
                      <td className="cell-mono">{r.model.replace(/^openrouter:/, "")}</td>
                      <td className="right num">{r.calls}</td>
                      <td className="right num">{fmtK(r.tokens)}</td>
                      <td className="right num">{r.cost.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </>
  );
}
