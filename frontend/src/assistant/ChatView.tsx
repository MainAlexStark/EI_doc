/** Один чат: лента сообщений, ход работы агента, виджеты, файлы, поле ввода. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  attachmentPath,
  authedUrl,
  cancelRun,
  decideAction,
  getConversation,
  getRun,
  openFile,
  regenerate,
  sendMessage,
  uploadAttachment,
  type Action,
  type Attachment,
  type ChatMessage,
  type FileRef,
  type ModelInfo,
  type RunState,
  type Source,
} from "./api";
import ModelPicker from "./ModelPicker";
import WidgetView from "./Widgets";

type Props = {
  conversationId: number;
  models: ModelInfo[];
  defaultModel: string;
  onChanged: () => void;
  onOpenContextFile: (id: number) => void;
  initialPrompt?: string;
};

type Turn =
  | { kind: "user"; message: ChatMessage }
  | { kind: "event"; message: ChatMessage }
  | { kind: "assistant"; messages: ChatMessage[] };

function groupTurns(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const m of messages) {
    if (m.role === "user" || m.role === "event") {
      turns.push({ kind: m.role, message: m });
      continue;
    }
    const last = turns[turns.length - 1];
    if (last?.kind === "assistant") last.messages.push(m);
    else turns.push({ kind: "assistant", messages: [m] });
  }
  return turns;
}

const POLL_MS = 600;

export default function ChatView({ conversationId, models, defaultModel, onChanged, onOpenContextFile, initialPrompt }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [run, setRun] = useState<RunState | null>(null);
  const [lastRun, setLastRun] = useState<RunState | null>(null);
  const [partial, setPartial] = useState<string>("");
  const [model, setModel] = useState(defaultModel);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const lastId = useRef(0);

  const scrollDown = useCallback(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getConversation(conversationId);
      setMessages(data.messages);
      lastId.current = data.messages.at(-1)?.id ?? 0;
      setRun(data.active_run);
      setLastRun(data.last_run);
      setPartial(data.partial ?? "");
      if (data.conversation.model) setModel(data.conversation.model);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    stickToBottom.current = true;
    setModel(defaultModel);
    void load();
  }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!model && defaultModel) setModel(defaultModel);
  }, [defaultModel, model]);

  // Опрос прогона, пока он идёт
  useEffect(() => {
    if (!run?.active) return;
    let stopped = false;
    const tick = async () => {
      try {
        const state = await getRun(run.id, lastId.current);
        if (stopped) return;
        if (state.messages.length) {
          setMessages((prev) => {
            const known = new Set(prev.map((m) => m.id));
            return [...prev, ...state.messages.filter((m) => !known.has(m.id))];
          });
          lastId.current = state.messages.at(-1)!.id;
        }
        setPartial(state.partial ?? "");
        if (state.run.active) {
          setRun(state.run);
          timer = window.setTimeout(tick, POLL_MS);
        } else {
          setRun(null);
          setLastRun(state.run);
          setPartial("");
          onChanged();
        }
      } catch (e) {
        if (!stopped) {
          setError((e as Error).message);
          timer = window.setTimeout(tick, POLL_MS * 4);
        }
      }
    };
    let timer = window.setTimeout(tick, 200);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [run?.id, run?.active, onChanged]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(scrollDown, [messages, partial, run?.activity, scrollDown]);

  // Виджеты и markdown меняют высоту уже после рендера — держим низ, пока человек не прокрутил вверх.
  const innerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = innerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => scrollDown());
    observer.observe(el);
    return () => observer.disconnect();
  }, [scrollDown]);

  const send = useCallback(
    async (text: string, attachments: Attachment[] = []) => {
      if (run?.active) return;
      setError("");
      stickToBottom.current = true;
      try {
        const res = await sendMessage(conversationId, text, attachments.map((a) => a.id), model);
        setMessages((prev) => [...prev, { ...res.message, attachments }]);
        lastId.current = res.message.id;
        setRun(res.run);
        onChanged();
      } catch (e) {
        setError((e as Error).message);
        throw e;
      }
    },
    [conversationId, model, run?.active, onChanged],
  );

  // Подсказка с экрана приветствия
  const sentInitial = useRef(false);
  useEffect(() => {
    if (initialPrompt && !loading && !sentInitial.current && messages.length === 0) {
      sentInitial.current = true;
      void send(initialPrompt).catch(() => undefined);
    }
  }, [initialPrompt, loading, messages.length, send]);

  const doRegenerate = async () => {
    try {
      const res = await regenerate(conversationId, model);
      const keepUntil = [...messages].reverse().find((m) => m.role === "user")?.id ?? 0;
      setMessages((prev) => prev.filter((m) => m.id <= keepUntil));
      lastId.current = keepUntil;
      setRun(res.run);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onAction = (updated: Action) => {
    setMessages((prev) =>
      prev.map((m) => (m.ui?.action?.id === updated.id ? { ...m, ui: { ...m.ui, action: updated } } : m)),
    );
    void getRun(lastRun?.id ?? run?.id ?? 0, lastId.current)
      .then((state) => {
        if (state.messages.length) {
          setMessages((prev) => {
            const known = new Set(prev.map((m) => m.id));
            return [...prev, ...state.messages.filter((m) => !known.has(m.id))];
          });
          lastId.current = state.messages.at(-1)!.id;
        }
      })
      .catch(() => void load());
  };

  const turns = useMemo(() => groupTurns(messages), [messages]);
  const lastTurnIsAssistant = turns.at(-1)?.kind === "assistant";

  return (
    <div className="chat">
      <div
        className="chat-scroll"
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        <div className="chat-inner" ref={innerRef}>
          {loading && messages.length === 0 && <div className="muted pad">Загрузка…</div>}
          {turns.map((turn, i) => {
            if (turn.kind === "user") return <UserBubble key={turn.message.id} message={turn.message} />;
            if (turn.kind === "event") return <EventLine key={turn.message.id} message={turn.message} />;
            const isLast = i === turns.length - 1;
            return (
              <AssistantTurn
                key={turn.messages[0].id}
                messages={turn.messages}
                live={isLast && !!run?.active}
                partial={isLast ? partial : ""}
                activity={isLast ? run?.activity ?? "" : ""}
                models={models}
                onSend={(t) => void send(t).catch(() => undefined)}
                onAction={onAction}
                onOpenContextFile={onOpenContextFile}
              />
            );
          })}
          {run?.active && !lastTurnIsAssistant && (
            <AssistantTurn
              messages={[]}
              live
              partial={partial}
              activity={run.activity}
              models={models}
              onSend={() => undefined}
              onAction={onAction}
              onOpenContextFile={onOpenContextFile}
            />
          )}
          {!run?.active && lastRun?.status === "error" && (
            <div className="chat-error">
              Ответ не получен: {lastRun.error}
              <button className="link" onClick={() => void doRegenerate()}>Повторить</button>
            </div>
          )}
          {!run?.active && lastRun?.status === "cancelled" && <div className="muted small center">Ответ остановлен</div>}
          {!run?.active && messages.length > 0 && lastTurnIsAssistant && (
            <div className="turn-tools">
              <button className="link" onClick={() => void doRegenerate()} title="Ответить заново выбранной моделью">
                ↻ Перегенерировать
              </button>
            </div>
          )}
        </div>
      </div>

      {error && <div className="chat-error bar">{error}</div>}

      <Composer
        conversationId={conversationId}
        running={!!run?.active}
        onSend={send}
        onStop={() => run && void cancelRun(run.id).then(setRun)}
        modelSlot={<ModelPicker models={models} value={model} onChange={setModel} compact />}
        vision={!!models.find((m) => m.id === model)?.vision}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
function UserBubble({ message }: { message: ChatMessage }) {
  return (
    <div className="msg-user">
      <div className="bubble">
        {message.attachments?.length > 0 && (
          <div className="att-row">
            {message.attachments.map((a) => <AttachmentChip key={a.id} attachment={a} />)}
          </div>
        )}
        {message.content && <div className="user-text">{message.content}</div>}
      </div>
    </div>
  );
}

function EventLine({ message }: { message: ChatMessage }) {
  return <div className="event-line">{message.content.replace(/^\[Событие\]\s*/, "")}</div>;
}

function AttachmentChip({ attachment }: { attachment: Attachment | FileRef }) {
  const [thumb, setThumb] = useState<string | null>(null);
  const isImage = attachment.mime?.startsWith("image/");
  useEffect(() => {
    if (isImage) void authedUrl(attachmentPath(attachment.id)).then(setThumb).catch(() => undefined);
  }, [attachment.id, isImage]);
  const ext = attachment.name.split(".").pop()?.toUpperCase() ?? "";
  const warn = "extract_error" in attachment && attachment.extract_error;
  return (
    <button
      className={`att-chip ${warn ? "warn" : ""}`}
      title={warn ? String(warn) : attachment.name}
      onClick={() => void openFile(attachmentPath(attachment.id), attachment.name)}
    >
      {thumb ? <img src={thumb} alt="" /> : <span className={`att-ext ext-${ext.toLowerCase()}`}>{ext}</span>}
      <span className="att-name">{attachment.name}</span>
    </button>
  );
}

function FileCard({ file }: { file: FileRef }) {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return (
    <div className="file-card">
      <span className={`att-ext ext-${ext}`}>{ext.toUpperCase()}</span>
      <div className="file-main">
        <b>{file.name}</b>
        <span className="muted small">{(file.size / 1024).toFixed(0)} КБ</span>
      </div>
      {(ext === "pdf" || ext === "md" || ext === "txt") && (
        <button onClick={() => void openFile(attachmentPath(file.id), file.name)}>Открыть</button>
      )}
      <button className="primary" onClick={() => void openFile(attachmentPath(file.id), file.name, true)}>Скачать</button>
    </div>
  );
}

const SOURCE_ICON: Record<string, string> = { rst: "🏛", web: "🌐", context: "📁", ei: "🗂", file: "📎", doc: "📄", mcp: "🔌" };

function SourcesRow({ sources, onOpenContextFile }: { sources: Source[]; onOpenContextFile: (id: number) => void }) {
  if (!sources.length) return null;
  return (
    <div className="sources">
      <span className="muted small">Источники:</span>
      {sources.map((s, i) => {
        const fileId = s.ref?.startsWith("file:") ? Number(s.ref.slice(5)) : null;
        const label = `${SOURCE_ICON[s.kind] ?? "•"} ${s.title}`;
        if (s.url) return <a key={i} className="src-chip" href={s.url} target="_blank" rel="noreferrer" title={s.url}>{label}</a>;
        if (fileId) return <button key={i} className="src-chip" onClick={() => onOpenContextFile(fileId)}>{label}</button>;
        return <span key={i} className="src-chip" title={s.ref}>{label}</span>;
      })}
    </div>
  );
}

function ActionCard({ action, onAction }: { action: Action; onAction: (a: Action) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decide = async (decision: "confirm" | "reject") => {
    setBusy(true);
    setError("");
    try {
      onAction(await decideAction(action.id, decision));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const payload = action.payload as Record<string, unknown>;
  const limits = (payload.limits ?? {}) as Record<string, unknown>;
  const rows: [string, unknown][] =
    action.kind === "si_type_create"
      ? [
          ["Номер в Госреестре", payload.registry_number],
          ["Наименование", payload.name],
          ["Изготовитель", payload.manufacturer],
          ["МПИ, мес.", payload.verification_interval_months],
          ["Ссылка ФГИС", payload.fif_url],
          ...Object.entries(limits).map(([k, v]) => [k, v] as [string, unknown]),
        ]
      : action.kind === "attestation_create"
        ? attestationRows(payload)
        : Object.entries(payload);
  return (
    <div className={`action-card st-${action.status}`}>
      <div className="action-head">
        <b>{action.title}</b>
        <span className="badge">
          {{ pending: "ждёт подтверждения", done: "выполнено", rejected: "отклонено", failed: "не выполнено" }[action.status]}
        </span>
      </div>
      <dl className="w-kv small">
        {rows.filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>
        ))}
      </dl>
      {action.sources?.length > 0 && (
        <details>
          <summary className="small">Откуда данные ({action.sources.length})</summary>
          <ul className="small">{action.sources.map((s, i) => <li key={i}>{s}</li>)}</ul>
        </details>
      )}
      {action.error && <div className="chat-error">{action.error}</div>}
      {error && <div className="chat-error">{error}</div>}
      {action.status === "pending" && (
        <div className="action-buttons">
          <button className="primary" disabled={busy} onClick={() => void decide("confirm")}>Подтвердить</button>
          <button disabled={busy} onClick={() => void decide("reject")}>Отклонить</button>
        </div>
      )}
    </div>
  );
}

/** Карточка аттестации ИО в чате: главное из плана, без JSON-простыни. */
function attestationRows(payload: Record<string, unknown>): [string, unknown][] {
  const e = (payload.equipment ?? {}) as Record<string, string>;
  const plan = (payload.plan ?? {}) as {
    program?: string; repeats?: number;
    points?: { label: string; role: string }[];
    modes?: { label: string; setpoint: string }[];
    characteristics?: { label: string; expr: string; tolerance: { kind: string; min?: string; max?: string; limit?: string } }[];
    layout?: unknown[];
  };
  const check = payload.plan_check as { ok: boolean; rows: { ok: boolean }[] } | null | undefined;
  const commission = (payload.commission ?? []) as { role: string; name: string }[];
  const instruments = (payload.instruments ?? []) as { name: string; serial_number: string; valid_until: string }[];
  const tol = (t: { kind: string; min?: string; max?: string; limit?: string }) =>
    t.kind === "range" ? `от ${t.min} до ${t.max}` : t.kind === "abs" ? `±${t.limit}` : "";
  return [
    ["Оборудование", [e.name, e.serial_number && `зав. № ${e.serial_number}`].filter(Boolean).join(", ")],
    ["Изготовитель, год", [e.manufacturer, e.year].filter(Boolean).join(", ")],
    ["Владелец", e.owner],
    ["Программа", plan.program],
    ["Режимы", (plan.modes ?? []).map((m) => m.label).join(", ")],
    ["Точки × измерений", `${plan.points?.length ?? 0} × ${plan.repeats ?? 0}`],
    ...(plan.characteristics ?? [])
      .map((c) => [c.label, [c.expr, tol(c.tolerance) && `допуск ${tol(c.tolerance)}`].filter(Boolean).join(" · ")] as [string, unknown]),
    ["Вид протокола", plan.layout?.length ? `по примеру (${plan.layout.length} блоков)` : "стандартный"],
    ["Сверка с примером", !check ? "не проводилась"
      : check.ok ? `сходится (${check.rows.length} значений)`
      : `НЕ сходится: ${check.rows.filter((r) => !r.ok).length} из ${check.rows.length}`],
    ["Комиссия", commission.map((m) => (m.role === "chair" ? `${m.name} (пред.)` : m.name)).join(", ")],
    ["Средства измерений", instruments.map((r) => `${r.name} № ${r.serial_number}${r.valid_until ? ` до ${r.valid_until}` : ""}`).join("; ")],
  ];
}

function argsSummary(args: Record<string, unknown> | undefined): string {
  if (!args) return "";
  const parts = Object.entries(args)
    .filter(([k, v]) => v !== "" && v != null && !["blocks", "facts", "sheets", "content", "widget", "sources", "limits"].includes(k))
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  const text = parts.join(" · ");
  return text.length > 140 ? `${text.slice(0, 139)}…` : text;
}

function AssistantTurn({
  messages, live, partial, activity, models, onSend, onAction, onOpenContextFile,
}: {
  messages: ChatMessage[];
  live: boolean;
  partial: string;
  activity: string;
  models: ModelInfo[];
  onSend: (text: string) => void;
  onAction: (a: Action) => void;
  onOpenContextFile: (id: number) => void;
}) {
  const results = new Map(messages.filter((m) => m.role === "tool").map((m) => [m.tool_call_id, m]));
  const sources: Source[] = [];
  const seen = new Set<string>();
  for (const m of messages) {
    for (const s of m.ui?.sources ?? []) {
      const key = `${s.kind}|${s.title}|${s.url ?? ""}`;
      if (!seen.has(key)) {
        seen.add(key);
        sources.push(s);
      }
    }
  }
  const stepCount = messages.reduce((n, m) => n + (m.tool_calls?.length ?? 0), 0);
  const assistantMessages = messages.filter((m) => m.role === "assistant");
  const finalText = assistantMessages.at(-1)?.content ?? "";
  const lastHasCalls = (assistantMessages.at(-1)?.tool_calls?.length ?? 0) > 0;
  const model = assistantMessages.at(-1)?.model;
  const [stepsOpen, setStepsOpen] = useState(false);
  const showSteps = stepsOpen || live;

  const blocks: JSX.Element[] = [];
  const outputs: JSX.Element[] = [];
  for (const m of assistantMessages) {
    const isFinal = m === assistantMessages.at(-1) && !lastHasCalls;
    if (m.content && !isFinal) {
      blocks.push(<div key={`t${m.id}`} className="step-thought"><Markdown text={m.content} /></div>);
    }
    for (const c of m.tool_calls ?? []) {
      const result = results.get(c.id);
      const ui = result?.ui ?? {};
      blocks.push(
        <details key={c.id} className={`step ${!result ? "running" : ui.error ? "failed" : "ok"}`}>
          <summary>
            <span className="step-icon">{!result ? <i className="spinner" /> : ui.error ? "✕" : "✓"}</span>
            <span className="step-label">{ui.label || c.name}</span>
            <span className="step-args">{argsSummary(c.args)}</span>
            {ui.summary && <span className="step-sum">{ui.summary}</span>}
          </summary>
          <pre className="step-result">{result ? result.content.slice(0, 6000) : "…"}</pre>
        </details>,
      );
      if (result) {
        for (const [i, w] of (ui.widgets ?? []).entries()) outputs.push(<WidgetView key={`w${result.id}-${i}`} widget={w} onSend={onSend} />);
        for (const f of ui.files ?? []) outputs.push(<FileCard key={`f${f.id}`} file={f} />);
        if (ui.action) outputs.push(<ActionCard key={`a${ui.action.id}`} action={ui.action} onAction={onAction} />);
      }
    }
  }

  return (
    <div className="msg-assistant">
      <div className="avatar" title="Ассистент-метролог">Σ</div>
      <div className="turn-body">
        {stepCount > 0 && (
          <div className="steps">
            <button className="steps-toggle" onClick={() => setStepsOpen((o) => !o)}>
              {live ? "Работаю" : "Ход работы"} · {stepCount} {plural(stepCount, ["шаг", "шага", "шагов"])} {showSteps ? "▴" : "▾"}
            </button>
            {showSteps && <div className="steps-list">{blocks}</div>}
          </div>
        )}
        {outputs}
        {!lastHasCalls && finalText && <Markdown text={finalText} />}
        {live && partial && <Markdown text={partial + " ▍"} />}
        {live && !partial && (
          <div className="activity"><i className="spinner" /> {activity || "Думаю…"}</div>
        )}
        {!live && <SourcesRow sources={sources} onOpenContextFile={onOpenContextFile} />}
        {!live && model && (
          <div className="turn-meta muted small">{models.find((x) => x.id === model)?.name ?? model}</div>
        )}
      </div>
    </div>
  );
}

function plural(n: number, forms: [string, string, string]) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return forms[1];
  return forms[2];
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          table: ({ node: _node, ...props }) => <div className="w-table-wrap"><table className="w-table" {...props} /></div>,
          code: ({ node: _node, className, children, ...props }) => (
            <code className={className} {...props}>
              {children}
            </code>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

// ---------------------------------------------------------------------------
type Pending = { key: string; file: File; attachment?: Attachment; error?: string };

function Composer({
  conversationId, running, onSend, onStop, modelSlot, vision,
}: {
  conversationId: number;
  running: boolean;
  onSend: (text: string, attachments: Attachment[]) => Promise<void>;
  onStop: () => void;
  modelSlot: JSX.Element;
  vision: boolean;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [drag, setDrag] = useState(false);
  const [sending, setSending] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const draftKey = `ei_doc_assistant_draft_${conversationId}`;

  useEffect(() => {
    try {
      setText(localStorage.getItem(draftKey) ?? "");
    } catch {
      setText("");
    }
    setPending([]);
  }, [draftKey]);

  useEffect(() => {
    try {
      if (text) localStorage.setItem(draftKey, text);
      else localStorage.removeItem(draftKey);
    } catch {
      /* ок */
    }
    const el = area.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 260)}px`;
    }
  }, [text, draftKey]);

  const addFiles = (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      const key = `${file.name}-${file.size}-${Math.random()}`;
      setPending((p) => [...p, { key, file }]);
      uploadAttachment(file, conversationId)
        .then((attachment) => setPending((p) => p.map((x) => (x.key === key ? { ...x, attachment } : x))))
        .catch((e) => setPending((p) => p.map((x) => (x.key === key ? { ...x, error: (e as Error).message } : x))));
    }
  };

  const uploading = pending.some((p) => !p.attachment && !p.error);
  const ready = pending.filter((p) => p.attachment).map((p) => p.attachment!);
  const canSend = !running && !sending && !uploading && (text.trim() !== "" || ready.length > 0);

  const submit = async () => {
    if (!canSend) return;
    setSending(true);
    try {
      await onSend(text.trim(), ready);
      setText("");
      setPending([]);
    } catch {
      /* ошибка показана выше */
    } finally {
      setSending(false);
      area.current?.focus();
    }
  };

  const hasImages = pending.some((p) => p.file.type.startsWith("image/"));

  return (
    <div
      className={`composer ${drag ? "drag" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
      }}
    >
      {pending.length > 0 && (
        <div className="att-row pending">
          {pending.map((p) => (
            <span key={p.key} className={`att-chip ${p.error ? "warn" : ""}`} title={p.error || p.attachment?.extract_error || ""}>
              {!p.attachment && !p.error && <i className="spinner" />}
              <span className="att-name">{p.file.name}</span>
              {p.attachment?.extract_error && <span className="warn-dot">!</span>}
              <button className="x" onClick={() => setPending((list) => list.filter((x) => x.key !== p.key))}>×</button>
            </span>
          ))}
        </div>
      )}
      {hasImages && !vision && (
        <div className="small warn-text">Выбранная модель не видит картинки — выберите модель со значком 👁</div>
      )}
      <textarea
        ref={area}
        rows={1}
        placeholder="Спросите про прибор, методику, поверку… Можно приложить PDF, Word, Excel, фото шильдика"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void submit();
          }
        }}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData.files);
          if (files.length) {
            e.preventDefault();
            addFiles(files);
          }
        }}
      />
      <div className="composer-bar">
        <button className="icon" title="Приложить файлы" onClick={() => fileInput.current?.click()}>📎</button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept=".pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md,.json,.html,image/*"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {modelSlot}
        <div className="spacer" />
        <span className="muted small hint">Enter — отправить, Shift+Enter — новая строка</span>
        {running ? (
          <button className="stop" onClick={onStop}>■ Стоп</button>
        ) : (
          <button className="primary send" disabled={!canSend} onClick={() => void submit()}>
            {sending ? "…" : "Отправить"}
          </button>
        )}
      </div>
    </div>
  );
}
