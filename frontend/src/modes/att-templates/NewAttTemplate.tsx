/** Новый шаблон аттестации: вид ИО + документы (пример протокола, программа, РЭ) → ассистент собирает план
 *  в своём чате (карточка «Подтвердить» сохраняет шаблон) → шаблон открывается сам. Или — из плана уже
 *  проведённой аттестации. */

import { useEffect, useRef, useState } from "react";
import { createConversation, fetchModels, sendMessage, uploadAttachment, type ModelInfo } from "../../assistant/api";
import ChatView from "../../assistant/ChatView";
import "../../assistant/assistant.css";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { SkeletonList } from "../../ui/Skeleton";
import { toast } from "../../ui/Toast";
import { Alert, Segmented } from "../../ui/controls";
import { FILE_ACCEPT } from "../si-templates/NewTemplate";
import Stepper from "../si-templates/Stepper";
import { EQUIPMENT_KINDS, fetchAttestationList, fetchPrograms, programFromAttestation, type AttestationBrief } from "./api";
import { ATT_STEPS } from "./ProgramView";

const ROLES = { example: "пример протокола", program: "программа аттестации", manual: "РЭ / паспорт", other: "другое" } as const;
type Role = keyof typeof ROLES;
type DocFile = { file: File; role: Role };

const SESSION_KEY = "ei_doc_att_tpl_build";
type Build = { conversation: number; kind: string; title: string; known: number[] };

const readBuild = (): Build | null => {
  try {
    return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
  } catch {
    return null;
  }
};

function prompt(kind: string, title: string, files: DocFile[], note: string) {
  const list = files.map((f) => `«${f.file.name}» — ${ROLES[f.role]}`).join("; ");
  return [
    `Собери шаблон протокола аттестации испытательного оборудования (ГОСТ Р 8.568-2017) для вида ИО «${kind}». Название шаблона: «${title}».`,
    list ? `Приложены документы: ${list}.` : "Документов не приложено — спроси, где взять программу аттестации и пример протокола.",
    "Порядок: 1) прочитай документы; 2) составь план — точки и группы, режимы с параметрами, характеристики с формулами-выражениями, допуски с источником (пункт документа), условия проведения, каркас протокола по примеру; " +
      "3) если в примере есть отсчёты и результаты — обязательно сверь формулы на числах примера (example) и объясни каждое расхождение, подгонять без объяснения нельзя; " +
      "4) предложи шаблон через ei_attestation_template_propose. Оборудование, комиссию, измерения и условия в шаблон не клади — их вносит человек.",
    note ? `Что важно учесть: ${note}` : "",
  ].filter(Boolean).join("\n\n");
}

export default function NewAttTemplate({
  initialKind, onCancel, onCreated,
}: {
  initialKind?: string;
  onCancel: () => void;
  onCreated: (programId: number) => void;
}) {
  const [build, setBuild] = useState<Build | null>(readBuild);
  const [source, setSource] = useState<"docs" | "attestation">("docs");
  return (
    <div className="att-page att-new">
      <Stepper steps={ATT_STEPS} current={build ? 1 : 0} />
      {build ? (
        <BuildChat
          build={build}
          onCreated={(id) => {
            sessionStorage.removeItem(SESSION_KEY);
            onCreated(id);
          }}
          onAbandon={() => {
            sessionStorage.removeItem(SESSION_KEY);
            setBuild(null);
          }}
        />
      ) : (
        <>
          <div className="si-new-head">
            <h2>Новый шаблон аттестации</h2>
            <p className="muted">
              Ассистент собирает план из документов: точки, режимы, характеристики с формулами и допуски с источником —
              и сверяет формулы на числах примера. Метролог проверяет; измерения потом вносит только человек.
            </p>
          </div>
          <Segmented
            value={source}
            onChange={setSource}
            options={[
              { value: "docs", label: "Из документов", icon: "sparkle" },
              { value: "attestation", label: "Из проведённой аттестации", icon: "history" },
            ]}
          />
          {source === "docs" ? (
            <DocsForm
              initialKind={initialKind}
              onCancel={onCancel}
              onStarted={(b) => {
                sessionStorage.setItem(SESSION_KEY, JSON.stringify(b));
                setBuild(b);
              }}
            />
          ) : (
            <FromAttestation onCancel={onCancel} onCreated={onCreated} />
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function DocsForm({ initialKind, onCancel, onStarted }: { initialKind?: string; onCancel: () => void; onStarted: (b: Build) => void }) {
  const [kind, setKind] = useState(initialKind ?? "");
  const [title, setTitle] = useState("");
  const [files, setFiles] = useState<DocFile[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const autoTitle = kind.trim() ? `${kind.trim()} — программа аттестации` : "";

  const guessRole = (name: string): Role =>
    /протокол|protocol/i.test(name) ? "example" : /программ|методик/i.test(name) ? "program" : /рэ|руковод|паспорт|manual/i.test(name) ? "manual" : "other";
  const add = (list: FileList | null) => list && setFiles((prev) => [...prev, ...Array.from(list).map((file) => ({ file, role: guessRole(file.name) }))]);

  const start = async () => {
    if (busy) return;
    if (!kind.trim()) return setError("Укажите вид оборудования — например, «Термостат»");
    if (!files.length) return setError("Приложите хотя бы один документ: пример протокола, программу аттестации или РЭ");
    setBusy(true);
    setError("");
    try {
      const [models, known] = await Promise.all([
        fetchModels().catch(() => ({ models: [] as ModelInfo[], default: "" })),
        fetchPrograms({ all: true, plan: false }),
      ]);
      const conversation = await createConversation(models.default || undefined);
      const attachments = [];
      for (const f of files) attachments.push(await uploadAttachment(f.file, conversation.id));
      const name = title.trim() || autoTitle;
      await sendMessage(conversation.id, prompt(kind.trim(), name, files, note.trim()), attachments.map((a) => a.id), models.default);
      onStarted({ conversation: conversation.id, kind: kind.trim(), title: name, known: known.map((p) => p.id) });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useHotkey("mod+enter", () => void start(), { allowInInputs: true, description: "Отдать документы ассистенту", group: "Шаблоны аттестации" });

  return (
    <div className="att-docs-form">
      <div className="form-grid">
        <div className="field">
          <label htmlFor="att-kind">Вид оборудования *</label>
          <input id="att-kind" list="att-kind-list" autoFocus value={kind} onChange={(e) => setKind(e.target.value)} placeholder="Термостат, шкаф сушильный…" />
          <datalist id="att-kind-list">{EQUIPMENT_KINDS.map((k) => <option key={k} value={k} />)}</datalist>
        </div>
        <div className="field">
          <label htmlFor="att-title">Название шаблона</label>
          <input id="att-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={autoTitle || "по умолчанию — вид + «программа аттестации»"} />
        </div>
      </div>
      <div className="att-kind-chips">
        {EQUIPMENT_KINDS.slice(0, 6).map((k) => (
          <button key={k} type="button" className={`chip ${kind === k ? "active" : ""}`} onClick={() => setKind(k)}>{k}</button>
        ))}
      </div>

      <section
        className={`card si-docs si-drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}
      >
        <div className="si-section-title"><Icon name="upload" size={15} /> Документы</div>
        <p className="sub">Лучше всего — пример заполненного протокола с отсчётами и результатами: по нему ассистент сверит формулы. Плюс программа аттестации и РЭ.</p>
        {files.length > 0 && (
          <ul className="si-files">
            {files.map((f, i) => (
              <li key={`${f.file.name}-${i}`}>
                <Icon name="file" size={14} /> <span className="grow">{f.file.name}</span>
                <select value={f.role} onChange={(e) => setFiles(files.map((x, j) => (j === i ? { ...x, role: e.target.value as Role } : x)))}>
                  {Object.entries(ROLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <button type="button" className="icon-btn" aria-label="Убрать" onClick={() => setFiles(files.filter((_, j) => j !== i))}><Icon name="x" size={13} /></button>
              </li>
            ))}
          </ul>
        )}
        <input ref={input} type="file" multiple hidden accept={FILE_ACCEPT} onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        <button type="button" className="sm" onClick={() => input.current?.click()}><Icon name="paperclip" size={14} /> Выбрать файлы</button>
        {files.length > 0 && !files.some((f) => f.role === "example") && (
          <p className="hint warn-text">Без примера протокола сверить формулы на числах не получится — метролог сверит их с методикой вручную.</p>
        )}
      </section>

      <div className="field">
        <label htmlFor="att-note">Что важно учесть (необязательно)</label>
        <textarea id="att-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="например: режимы +5 и +8 °С; 9 точек, центр — контрольная" />
      </div>
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      <div className="si-step-actions">
        <button type="button" onClick={onCancel}>Отмена</button>
        <button type="button" className="primary" disabled={busy} onClick={() => void start()}>
          {busy ? "Передаю ассистенту…" : "Собрать план с ассистентом"} {!busy && <Icon name="arrowRight" size={16} />}
          <Kbd keys="mod+enter" className="on-accent kbd-hint" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function BuildChat({ build, onCreated, onAbandon }: { build: Build; onCreated: (id: number) => void; onAbandon: () => void }) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [found, setFound] = useState<{ id: number; title: string } | null>(null);
  const known = useRef(new Set(build.known));

  useEffect(() => {
    fetchModels().then((d) => { setModels(d.models); setDefaultModel(d.default); }).catch(() => undefined);
  }, []);

  const check = useRef(async () => {
    try {
      const list = await fetchPrograms({ all: true, plan: false });
      const fresh = list.find((p) => !known.current.has(p.id) && (p.created_via_assistant || p.title === build.title));
      if (fresh) {
        setFound({ id: fresh.id, title: fresh.title });
        toast.success("Шаблон сохранён", { description: fresh.title, action: { label: "Открыть", onClick: () => onCreated(fresh.id) } });
        return true;
      }
    } catch {
      /* опрос — тихо, следующий раз */
    }
    return false;
  });

  useEffect(() => {
    if (found) return;
    let timer = 0;
    const tick = async () => {
      if (await check.current()) return;
      timer = window.setTimeout(tick, 5000);
    };
    timer = window.setTimeout(tick, 3000);
    return () => window.clearTimeout(timer);
  }, [found]);

  return (
    <div className="att-build">
      <div className="att-build-chat">
        <ChatView
          conversationId={build.conversation}
          models={models}
          defaultModel={defaultModel}
          onChanged={() => void check.current()}
          onOpenContextFile={() => undefined}
        />
      </div>
      <aside className="att-build-side card">
        <b>{build.title}</b>
        <span className="sub">вид ИО: {build.kind}</span>
        {found ? (
          <>
            <Alert tone="ok">Шаблон «{found.title}» сохранён. Дальше — сверка формул и правка плана.</Alert>
            <button type="button" className="primary" onClick={() => onCreated(found.id)}>Открыть шаблон <Icon name="arrowRight" size={16} /></button>
          </>
        ) : (
          <>
            <ol className="att-build-steps">
              <li>Ассистент читает документы и собирает план.</li>
              <li>Сверяет формулы на числах примера — отчёт будет в чате.</li>
              <li>Показывает карточку шаблона — нажмите в ней «Подтвердить».</li>
              <li>Шаблон откроется здесь: таблицы точек, режимов, характеристик.</li>
            </ol>
            <p className="sub">Правки — словами в чате: «центр — контрольная точка», «допуск ±2 по п. 7.3».</p>
          </>
        )}
        <button type="button" className="link" onClick={onAbandon}>Начать заново</button>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
function FromAttestation({ onCancel, onCreated }: { onCancel: () => void; onCreated: (id: number) => void }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<AttestationBrief[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      fetchAttestationList(query).then(setItems).catch((e) => setError((e as Error).message));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const make = async (a: AttestationBrief) => {
    setBusy(a.id);
    setError("");
    try {
      const p = await programFromAttestation(a.id);
      toast.success("Шаблон сделан по аттестации", { description: p.title });
      onCreated(p.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="att-docs-form">
      <p className="sub">План проведённой аттестации станет шаблоном вида: структура протокола без конкретного ИО и измерений. Проверенный метрологом план останется проверенным.</p>
      <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Наименование, заводской номер, владелец" />
      {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      {items === null ? (
        <SkeletonList rows={3} />
      ) : items.length === 0 ? (
        <p className="hint">Аттестаций не нашлось.</p>
      ) : (
        <div className="si-rows">
          {items.slice(0, 30).map((a) => (
            <button key={a.id} type="button" className="si-row" disabled={busy !== null} onClick={() => void make(a)}>
              <span className="si-row-icon"><Icon name="shield" size={16} /></span>
              <span className="si-row-main">
                <b>{a.name}</b>
                <span className="sub"><span className="data">зав. № {a.serial_number}</span> · {a.owner}{a.number ? ` · протокол № ${a.number}` : ""}</span>
              </span>
              {a.plan_approved ? <span className="pill ok">план проверен</span> : <span className="pill warn">план не проверен</span>}
              {busy === a.id ? <span className="spinner sm" /> : <Icon name="plus" size={16} />}
            </button>
          ))}
        </div>
      )}
      <div className="si-step-actions"><button type="button" onClick={onCancel}>Отмена</button></div>
    </div>
  );
}
