/** «Шаблоны протоколов» — режим «Создать шаблон протокола поверки».
 *
 * Слева — мои шаблоны в работе (по одному чату на тип СИ). Новый: поиск по Госреестру (ФИФ) →
 * выбор типа → агент получает методику поверки и все документы типа и сам делает первый черновик.
 * В середине — чат с агентом этого типа, справа — протокол по черновику: перерисовывается после
 * каждого сохранения агента. Стрелка вправо вверху («Продолжить») делает черновик действующим
 * шаблоном типа — дальше по нему создаются протоколы на экране «Поверки».
 *
 * Сервер — apps/assistant/template_workspace.py, /api/assistant/template-workspaces/.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import "./assistant.css";
import { downloadFile } from "../api";
import FifTypeSearch from "../components/FifTypeSearch";
import { loadPresets } from "../components/SiTypeForm";
import Icon from "../ui/Icon";
import { Modal } from "../ui/Modal";
import { Alert, PageActions, Segmented, Spinner } from "../ui/controls";
import { PHONE, useMediaQuery } from "../ui/hooks";
import {
  fetchModels,
  getWorkspace,
  listWorkspaces,
  openWorkspace,
  publishWorkspace,
  setWorkspaceFamily,
  workspacePreview,
  workspacePreviewPath,
  type ModelInfo,
  type TemplateDigest,
  type TemplateWorkspace,
} from "./api";
import ChatView from "./ChatView";

const HASH = "#/protocol-templates";

/** `#/protocol-templates/<id типа СИ>` — пришли с экрана поверки или из справочника: сразу открыть этот тип. */
function siTypeFromHash(): number | null {
  const match = window.location.hash.match(/^#\/protocol-templates\/(\d+)/);
  return match ? Number(match[1]) : null;
}

export default function TemplateStudio() {
  const phone = useMediaQuery(PHONE);
  const [items, setItems] = useState<TemplateWorkspace[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [listOpen, setListOpen] = useState(!phone);
  const [opening, setOpening] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [error, setError] = useState("");

  const loadList = useCallback(() => {
    listWorkspaces().then(setItems).catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    loadList();
    fetchModels()
      .then((d) => {
        setModels(d.models);
        setDefaultModel(d.default);
      })
      .catch(() => undefined);
  }, [loadList]);

  const open = useCallback(
    async (body: { si_type?: number; fif_uuid?: string; name?: string }) => {
      setOpening(true);
      setError("");
      try {
        const workspace = await openWorkspace(body);
        setSearchOpen(false);
        setCurrent(workspace.id);
        if (phone) setListOpen(false);
        loadList();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setOpening(false);
      }
    },
    [loadList, phone],
  );

  // Пришли по ссылке «Создать шаблон протокола» для конкретного типа (с экрана поверки, из справочника).
  useEffect(() => {
    const fromHash = () => {
      const siType = siTypeFromHash();
      if (siType) {
        window.history.replaceState(null, "", HASH);
        void open({ si_type: siType });
      }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, [open]);

  const select = (id: number) => {
    setCurrent(id);
    if (phone) setListOpen(false);
  };

  return (
    <div className={`assistant tpl-studio ${listOpen ? "" : "no-sidebar"}`}>
      <PageActions>
        <button type="button" onClick={() => setSearchOpen(true)} aria-label="Новый шаблон">
          <Icon name="plus" size={16} strokeWidth={2.2} />
          <span className="hide-phone">Новый шаблон</span>
        </button>
      </PageActions>

      {listOpen && (
        <aside className="conv-sidebar">
          <div className="conv-group">Шаблоны в работе</div>
          <div className="conv-list">
            {items.map((w) => (
              <div
                key={w.id}
                className={`conv-item tpl-item ${current === w.id ? "active" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => select(w.id)}
                onKeyDown={(e) => e.key === "Enter" && select(w.id)}
              >
                <span className="conv-title">
                  {w.si_type.name}
                  <small>
                    № {w.si_type.registry_number} · {w.status === "published" && !w.has_draft ? "действует" : w.has_draft ? `черновик, ред. ${w.revision}` : "готовится"}
                  </small>
                </span>
                {w.agent_busy && <span className="spinner sm" title="Ассистент работает" />}
                {w.status === "published" && !w.has_draft && <Icon name="checkCircle" size={14} className="ok-text" />}
              </div>
            ))}
            {items.length === 0 && (
              <div className="conv-empty">Пока пусто. «Новый шаблон» → найдите тип в Госреестре — ассистент соберёт шаблон по методике поверки.</div>
            )}
          </div>
        </aside>
      )}

      <section className="assistant-main">
        {error && <div className="assistant-alert"><Alert onClose={() => setError("")}>{error}</Alert></div>}
        {opening && <Spinner label="Открываю тип и передаю ассистенту методику…" />}
        {!opening && current ? (
          <Workspace
            key={current}
            id={current}
            models={models}
            defaultModel={defaultModel}
            listOpen={listOpen}
            onToggleList={() => setListOpen((o) => !o)}
            onChanged={loadList}
          />
        ) : (
          !opening && (
            <div className="welcome">
              <div className="welcome-inner tpl-welcome">
                <div className="welcome-logo"><Icon name="layers" size={28} strokeWidth={1.8} /></div>
                <h2>Шаблон протокола поверки</h2>
                <p className="muted">
                  Найдите тип СИ в Госреестре. Ассистент прочитает методику поверки и документы типа из ФИФ и соберёт
                  шаблон протокола — он появится справа от чата. Правьте его словами, а когда всё верно — нажмите
                  «Продолжить» вверху: по шаблону можно будет создавать протоколы этого типа.
                </p>
                <div className="card tpl-search">
                  <FifTypeSearch
                    autoFocus={!phone}
                    label="Тип СИ"
                    inputId="tpl-type-search"
                    onPickFif={(draft) =>
                      draft.fif_uuid
                        ? void open({ fif_uuid: draft.fif_uuid, name: draft.name })
                        : setError("Шаблон делается по документам из Госреестра — выберите тип из подсказок ФИФ")
                    }
                    onPickLocal={(item) => item.si_type_id && void open({ si_type: item.si_type_id })}
                  />
                </div>
              </div>
            </div>
          )
        )}
      </section>

      <Modal open={searchOpen} onClose={() => setSearchOpen(false)} size="lg" title="Новый шаблон протокола"
        subtitle="Выберите тип из Госреестра — методика и документы уйдут ассистенту">
        {searchOpen && (
          <FifTypeSearch
            autoFocus
            inputId="tpl-type-search-modal"
            onPickFif={(draft) =>
              draft.fif_uuid
                ? void open({ fif_uuid: draft.fif_uuid, name: draft.name })
                : setError("Шаблон делается по документам из Госреестра — выберите тип из подсказок ФИФ")
            }
            onPickLocal={(item) => item.si_type_id && void open({ si_type: item.si_type_id })}
          />
        )}
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Рабочее место одного типа: чат + протокол
// ---------------------------------------------------------------------------
type Families = { id: number; name: string; calculator_key: string }[];

function Workspace({
  id,
  models,
  defaultModel,
  listOpen,
  onToggleList,
  onChanged,
}: {
  id: number;
  models: ModelInfo[];
  defaultModel: string;
  listOpen: boolean;
  onToggleList: () => void;
  onChanged: () => void;
}) {
  const phone = useMediaQuery(PHONE);
  const narrow = useMediaQuery("(max-width: 1180px)");
  const [ws, setWs] = useState<TemplateWorkspace | null>(null);
  const [families, setFamilies] = useState<Families>([]);
  const [pane, setPane] = useState<"chat" | "protocol">("chat");
  const [publishing, setPublishing] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const busyRef = useRef(false);
  const lastRef = useRef<TemplateWorkspace | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getWorkspace(id);
      const prev = lastRef.current;
      // Новая редакция или агент закончил — обновить и список слева.
      if (prev && (prev.revision !== data.revision || prev.agent_busy !== data.agent_busy)) onChanged();
      lastRef.current = data;
      busyRef.current = data.agent_busy;
      setWs(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id, onChanged]);

  // Черновик меняет агент посреди своего ответа — опрашиваем чаще, пока он работает.
  useEffect(() => {
    void load();
    let timer = 0;
    const tick = () => {
      timer = window.setTimeout(async () => {
        await load();
        tick();
      }, busyRef.current ? 2000 : 6000);
    };
    tick();
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    loadPresets()
      .then((p) => setFamilies(p.families))
      .catch(() => undefined);
  }, []);

  const template = ws ? ws.draft ?? ws.published ?? null : null;
  const canPublish = Boolean(ws?.draft) && !ws?.si_type.is_water;

  const publish = async () => {
    if (!ws) return;
    setPublishing(true);
    setError("");
    try {
      const data = await publishWorkspace(ws.id);
      setWs(data);
      setNotice(
        data.published?.needs_review
          ? "Шаблон действует: на экране «Поверки» по этому типу теперь протокол по шаблону. Метролог проверит шаблон — до этого протокол не подписывается."
          : "Шаблон действует: на экране «Поверки» по этому типу теперь протокол по шаблону.",
      );
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPublishing(false);
    }
  };

  const changeFamily = async (family: number) => {
    if (!ws) return;
    try {
      setWs(await setWorkspaceFamily(ws.id, family));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!ws) return error ? <div className="assistant-alert"><Alert>{error}</Alert></div> : <Spinner />;

  const nextButton = (
    <button
      type="button"
      className="primary tpl-next"
      disabled={!canPublish || publishing}
      onClick={() => void publish()}
      title={
        canPublish
          ? "Продолжить: черновик станет действующим шаблоном — по нему можно создавать протоколы"
          : ws.status === "published"
            ? "Шаблон уже действует. Новые правки ассистента — в черновик, затем снова «Продолжить»"
            : "Дождитесь, пока ассистент сохранит черновик"
      }
    >
      {ws.status === "published" && !ws.draft ? (
        <>
          <Icon name="check" size={16} /> Действует
        </>
      ) : (
        <>
          Продолжить <Icon name="arrowRight" size={16} strokeWidth={2.2} />
        </>
      )}
    </button>
  );

  const chat = (
    <ChatView
      conversationId={ws.conversation}
      models={models}
      defaultModel={defaultModel}
      onChanged={() => void load()}
      onOpenContextFile={() => undefined}
    />
  );
  const protocol = <ProtocolPane ws={ws} template={template} />;

  return (
    <>
      <div className="assistant-bar tpl-bar">
        <button className={`icon-btn ${listOpen ? "active" : ""}`} title="Шаблоны в работе" aria-label="Шаблоны в работе" onClick={onToggleList}>
          <Icon name="sidebar" />
        </button>
        <div className="tpl-bar-title">
          <b className="assistant-title">{ws.si_type.name}</b>
          <span className="sub">
            № {ws.si_type.registry_number}
            {ws.si_type.manufacturer ? ` · ${ws.si_type.manufacturer}` : ""}
          </span>
        </div>
        {families.length > 0 && (
          <label className="tpl-family" title="Вид СИ — от него зависит, как поверяется тип: счётчики воды — калькулятором, остальное — по шаблону">
            <span className="hide-phone">Вид СИ</span>
            <select value={ws.si_type.family} onChange={(e) => void changeFamily(Number(e.target.value))}>
              {families.map((f) => (
                <option key={f.id} value={f.id}>{f.name}</option>
              ))}
            </select>
          </label>
        )}
        <span className={`pill ${ws.status === "published" && !ws.draft ? "ok" : "muted"}`}>
          {ws.status === "published" && !ws.draft ? "действует" : ws.draft ? `черновик · ред. ${ws.revision}` : "готовится"}
        </span>
      </div>
      {/* «Продолжить» — вверху страницы, рядом с «Новый шаблон». */}
      <PageActions>{nextButton}</PageActions>
      {error && <div className="assistant-alert"><Alert onClose={() => setError("")}>{error}</Alert></div>}
      {notice && (
        <div className="assistant-alert">
          <Alert tone="ok" onClose={() => setNotice("")}>
            {notice}{" "}
            <a href="#/field-work" className="link">Перейти к поверкам</a>
          </Alert>
        </div>
      )}
      {ws.si_type.is_water && (
        <div className="assistant-alert">
          <Alert tone="warn">
            Тип стоит в «{ws.si_type.family_name}» — их поверяет встроенный калькулятор, шаблон не нужен. Если это не
            счётчик воды (вид угадался неверно), выберите правильный «Вид СИ» вверху.
          </Alert>
        </div>
      )}
      {narrow ? (
        <>
          <div className="tpl-switch">
            <Segmented
              size="sm"
              value={pane}
              onChange={setPane}
              options={[
                { value: "chat", label: "Чат", icon: "sparkle" },
                { value: "protocol", label: phone ? "Протокол" : `Протокол${ws.draft ? ` · ред. ${ws.revision}` : ""}`, icon: "doc" },
              ]}
            />
          </div>
          <div className="tpl-single">{pane === "chat" ? chat : protocol}</div>
        </>
      ) : (
        <div className="tpl-split">
          <div className="tpl-chat">{chat}</div>
          {protocol}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Протокол по черновику
// ---------------------------------------------------------------------------
function ProtocolPane({ ws, template }: { ws: TemplateWorkspace; template: TemplateDigest | null }) {
  const [view, setView] = useState<"pdf" | "parts">("pdf");
  const [url, setUrl] = useState("");
  const [pdfError, setPdfError] = useState("");
  const [loading, setLoading] = useState(false);
  const hasTemplate = Boolean(template);

  const urlRef = useRef("");
  useEffect(() => {
    if (!hasTemplate) return;
    let cancelled = false;
    setLoading(true);
    workspacePreview(ws.id, ws.revision)
      .then((made) => {
        if (cancelled) return URL.revokeObjectURL(made);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = made;
        setUrl(made);
        setPdfError("");
      })
      .catch((e) => !cancelled && setPdfError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ws.id, ws.revision, hasTemplate]);

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  if (!template) {
    return (
      <div className="tpl-protocol empty">
        <div className="tpl-wait">
          {ws.agent_busy ? <span className="spinner" /> : <Icon name="doc" size={28} />}
          <b>{ws.agent_busy ? "Ассистент собирает шаблон…" : "Черновика пока нет"}</b>
          <span className="muted">
            {ws.agent_busy
              ? ws.agent_activity || "Читает методику поверки и документы типа из ФИФ"
              : "Напишите ассистенту, что нужно, — он сохранит черновик, и протокол появится здесь."}
          </span>
          {ws.documents && ws.documents.length > 0 && <Documents docs={ws.documents} />}
        </div>
      </div>
    );
  }

  const which = ws.draft ? `Черновик · ред. ${ws.revision}` : "Действующий шаблон";
  return (
    <div className="tpl-protocol">
      <div className="tpl-protocol-bar">
        <Segmented
          size="sm"
          value={view}
          onChange={setView}
          options={[
            { value: "pdf", label: "Протокол" },
            { value: "parts", label: "Состав" },
          ]}
        />
        <span className="sub grow">
          {which}
          {ws.agent_busy && <> · <span className="spinner sm" /> ассистент правит</>}
          {loading && !ws.agent_busy && " · обновляю…"}
        </span>
        <button type="button" className="sm" title="Скачать пустой протокол в Word"
          onClick={() => void downloadFile(workspacePreviewPath(ws.id, "docx", ws.revision, false), `Шаблон протокола — ${ws.si_type.name}.docx`)}>
          <Icon name="download" size={14} /> Word
        </button>
      </div>
      {view === "pdf" ? (
        pdfError ? (
          <div className="tpl-parts">
            <Alert tone="warn">PDF не собрался: {pdfError}. Ниже — состав шаблона; Word скачивается кнопкой выше.</Alert>
            <Parts template={template} ws={ws} />
          </div>
        ) : url ? (
          <iframe className="tpl-pdf" src={`${url}#toolbar=0&navpanes=0&view=FitH`} title="Протокол по шаблону" />
        ) : (
          <Spinner label="Собираю протокол…" />
        )
      ) : (
        <div className="tpl-parts">
          <Parts template={template} ws={ws} />
        </div>
      )}
    </div>
  );
}

function Parts({ template, ws }: { template: TemplateDigest; ws: TemplateWorkspace }) {
  const rows: [string, string | string[]][] = [
    ["Методика", template.method || "—"],
    ["Вид СИ", template.si_kind || ws.si_type.type_title || "—"],
    ["Параметры прибора", template.params.map((p) => `${p.label}${p.unit ? `, ${p.unit}` : ""} — ${p.from_type ? "из типа" : "вносит поверитель"}`)],
    ["Операции поверки", template.operations],
    ["Поверяемые точки", template.modes],
    ["Величины в точке", template.points.map((p) => (template.repeats && template.repeats > 1 ? `${p} × ${template.repeats}` : p))],
    ["Характеристики", template.characteristics],
    ["Наборы эталонов", template.kits.length ? template.kits : ["не выбраны — метрологу добавить в перечень"]],
    ["Источники", template.sources],
  ];
  return (
    <>
      <dl className="tpl-dl">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {Array.isArray(value) ? (
                value.length ? <ul>{value.map((v, i) => <li key={i}>{v}</li>)}</ul> : "—"
              ) : (
                value
              )}
            </dd>
          </div>
        ))}
        <div>
          <dt>Сверка с примером</dt>
          <dd>{template.check == null ? "нет примера в методике" : template.check.ok ? "формулы сходятся" : <span className="warn-text">есть расхождения</span>}</dd>
        </div>
      </dl>
      {ws.documents && ws.documents.length > 0 && <Documents docs={ws.documents} />}
    </>
  );
}

function Documents({ docs }: { docs: NonNullable<TemplateWorkspace["documents"]> }) {
  return (
    <div className="tpl-docs">
      <div className="conv-group">Документы типа из ФИФ</div>
      {docs.map((d) => (
        <button key={d.id} type="button" className="link" onClick={() => void downloadFile(d.file_url, `${d.title}.pdf`)}>
          <Icon name="file" size={13} /> {d.kind_display}: {d.title}
          {!d.downloaded && <span className="muted"> (ещё не скачан)</span>}
        </button>
      ))}
    </div>
  );
}
