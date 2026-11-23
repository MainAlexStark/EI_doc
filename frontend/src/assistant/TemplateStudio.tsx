/** Студия шаблона протокола поверки — шаги 3 и 4 режима «Шаблоны поверки СИ» (modes/si-templates).
 *
 * Слева — чат с ассистентом этого типа СИ (свой чат на тип), справа — живой протокол по черновику:
 * перерисовывается после каждого сохранения агента, что изменилось — подсвечено. «Продолжить» вверху
 * открывает шаг 4: проверка и публикация — черновик становится действующим шаблоном типа, метролог
 * отмечает «проверен», и по типу создаются протоколы (режим «Приёмки»).
 *
 * Клавиши: ⌘Enter — отправить сообщение, ⌘S — сохранить черновик (его сохраняет ассистент — покажем
 * редакцию), ⌘. — переключить «Протокол / Состав».
 *
 * Сервер — apps/assistant/template_workspace.py, /api/assistant/template-workspaces/.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./assistant.css";
import "../modes/si-templates/si-templates.css";
import { approveVerificationTemplate, downloadFile } from "../api";
import { loadPresets } from "../components/SiTypeForm";
import type { Go } from "../modes/types";
import Stepper from "../modes/si-templates/Stepper";
import {
  DIGEST_KEYS,
  DIGEST_LABEL,
  STATUS_LABEL,
  STATUS_PILL,
  diffDigest,
  dropTemplatesCache,
  workspaceStatus,
  type DigestDiff,
  type DigestKey,
} from "../modes/si-templates/api";
import { useHotkey } from "../ui/Hotkeys";
import Icon from "../ui/Icon";
import Kbd from "../ui/Kbd";
import { Sheet } from "../ui/Sheet";
import { Skeleton, SkeletonText } from "../ui/Skeleton";
import { toast } from "../ui/Toast";
import { Alert, PageActions, Segmented } from "../ui/controls";
import { PHONE, useMediaQuery } from "../ui/hooks";
import {
  getWorkspace,
  publishWorkspace,
  sendMessage,
  setWorkspaceFamily,
  uploadAttachment,
  workspacePreview,
  workspacePreviewPath,
  type ModelInfo,
  type TemplateDigest,
  type TemplateWorkspace,
} from "./api";
import ChatView from "./ChatView";

export const STUDIO_STEPS = [
  { key: "type", label: "Тип СИ", hint: "Поиск в Госреестре (ФИФ)" },
  { key: "docs", label: "Документы", hint: "Методика, описание типа, свои файлы" },
  { key: "studio", label: "Шаблон с ассистентом", hint: "Чат и живой протокол" },
  { key: "publish", label: "Проверка и публикация", hint: "Метролог проверяет — по типу создаются протоколы" },
];

/** Файлы и пожелание с шага 2 — передаются ассистенту, когда он закончит первый черновик. */
export type PendingHandoff = { files: File[]; note: string };

type Families = { id: number; name: string; calculator_key: string }[];

export default function TemplateStudio({
  id,
  models,
  defaultModel,
  isReviewer,
  handoff,
  onHandoffDone,
  onChanged,
  onBack,
  onStepBack,
  go,
}: {
  id: number;
  models: ModelInfo[];
  defaultModel: string;
  isReviewer: boolean;
  handoff?: PendingHandoff | null;
  onHandoffDone?: () => void;
  onChanged: () => void;
  onBack: () => void;
  /** Клик по пройденному шагу 1–2: вернуться к выбору типа. */
  onStepBack?: (step: 0 | 1) => void;
  go: Go;
}) {
  const phone = useMediaQuery(PHONE);
  const narrow = useMediaQuery("(max-width: 1180px)");
  const [ws, setWs] = useState<TemplateWorkspace | null>(null);
  const [families, setFamilies] = useState<Families>([]);
  const [pane, setPane] = useState<"chat" | "protocol">("chat");
  const [view, setView] = useState<"pdf" | "parts">("pdf");
  const [publishOpen, setPublishOpen] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [chatKey, setChatKey] = useState(0);
  const [diff, setDiff] = useState<{ revision: number; diff: DigestDiff } | null>(null);
  const [handoffState, setHandoffState] = useState<"" | "waiting" | "sending">(handoff && (handoff.files.length || handoff.note) ? "waiting" : "");
  const busyRef = useRef(false);
  const lastRef = useRef<TemplateWorkspace | null>(null);
  const handoffRef = useRef(handoff);

  const load = useCallback(async () => {
    try {
      const data = await getWorkspace(id);
      const prev = lastRef.current;
      if (prev && (prev.revision !== data.revision || prev.agent_busy !== data.agent_busy)) onChanged();
      if (prev && prev.revision !== data.revision) {
        const before = prev.draft ?? prev.published ?? null;
        const after = data.draft ?? data.published ?? null;
        const changed = diffDigest(before, after);
        if (changed) setDiff({ revision: data.revision, diff: changed });
      }
      lastRef.current = data;
      busyRef.current = data.agent_busy;
      setWs(data);
      setLoadError("");
    } catch (e) {
      setLoadError((e as Error).message);
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

  // Шаг 2 → ассистенту: свои файлы и пожелание, как только он свободен (первый черновик готов).
  useEffect(() => {
    const pending = handoffRef.current;
    if (!ws || ws.agent_busy || !pending || handoffState !== "waiting") return;
    setHandoffState("sending");
    void (async () => {
      try {
        const attachments = [];
        for (const file of pending.files) attachments.push(await uploadAttachment(file, ws.conversation));
        const names = attachments.map((a) => `«${a.name}»`).join(", ");
        const text = [
          attachments.length ? `Приложил свои документы к типу: ${names}. Прочитай их и учти в шаблоне (если расходятся с ФИФ — скажи, где).` : "",
          pending.note ? `Что важно учесть: ${pending.note}` : "",
        ].filter(Boolean).join("\n\n");
        await sendMessage(ws.conversation, text, attachments.map((a) => a.id), defaultModel);
        handoffRef.current = null;
        setHandoffState("");
        onHandoffDone?.();
        setChatKey((k) => k + 1);
        void load();
      } catch (e) {
        setHandoffState("waiting");
        handoffRef.current = null;
        setError(`Не удалось передать ваши файлы ассистенту: ${(e as Error).message}. Приложите их в чате скрепкой.`);
      }
    })();
  }, [ws, handoffState, defaultModel, load, onHandoffDone]);

  const template = ws ? ws.draft ?? ws.published ?? null : null;
  const status = ws ? workspaceStatus(ws) : "draft";

  // --- клавиши -------------------------------------------------------------
  useHotkey(
    "mod+enter",
    () => {
      const area = document.querySelector<HTMLTextAreaElement>(".si-studio .tpl-chat textarea, .si-studio .tpl-single textarea");
      if (!area || !area.value.trim()) return;
      area.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    },
    { allowInInputs: true, description: "Отправить сообщение ассистенту", group: "Шаблоны поверки", enabled: !publishOpen },
  );
  useHotkey(
    "mod+s",
    () => {
      if (!ws) return;
      if (ws.agent_busy) toast.info("Ассистент правит черновик — сохранит сам, редакция обновится справа");
      else if (ws.draft) toast.success(`Черновик сохранён — ред. ${ws.revision}`, { description: "Его сохраняет ассистент после каждой правки" });
      else toast.info(ws.published ? "Правок нет — действует опубликованный шаблон" : "Черновика пока нет — напишите ассистенту, что нужно");
    },
    { allowInInputs: true, description: "Сохранить черновик", group: "Шаблоны поверки" },
  );
  useHotkey("mod+.", () => setView((v) => (v === "pdf" ? "parts" : "pdf")), {
    allowInInputs: true, description: "Протокол ↔ состав шаблона", group: "Шаблоны поверки",
  });

  const changeFamily = async (family: number) => {
    if (!ws) return;
    try {
      setWs(await setWorkspaceFamily(ws.id, family));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!ws) {
    return (
      <div className="si-studio">
        {loadError ? (
          <div className="si-studio-error">
            <Alert>Рабочее место не открылось: {loadError}</Alert>
            <div className="si-step-actions">
              <button type="button" onClick={onBack}>К списку шаблонов</button>
              <button type="button" className="primary" onClick={() => void load()}>Повторить</button>
            </div>
          </div>
        ) : (
          <StudioSkeleton />
        )}
      </div>
    );
  }

  const published = ws.status === "published" && !ws.draft;
  const canPublish = Boolean(ws.draft) && !ws.si_type.is_water;
  const nextButton = (
    <button
      type="button"
      className={`tpl-next ${published ? "" : "primary"}`}
      disabled={!ws.draft && !published}
      onClick={() => setPublishOpen(true)}
      title={
        canPublish
          ? "Продолжить: проверка и публикация — по шаблону начнут создаваться протоколы"
          : published
            ? "Шаблон действует — посмотреть статус проверки"
            : "Дождитесь, пока ассистент сохранит черновик"
      }
    >
      {published ? (
        <>
          <Icon name={status === "reviewed" ? "checkCircle" : "clock"} size={16} /> {status === "reviewed" ? "Проверен" : "На проверке"}
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
      key={`${ws.conversation}-${chatKey}`}
      conversationId={ws.conversation}
      models={models}
      defaultModel={defaultModel}
      onChanged={() => void load()}
      onOpenContextFile={() => undefined}
    />
  );
  const protocol = (
    <ProtocolPane
      ws={ws}
      template={template}
      view={view}
      onView={setView}
      diff={diff && diff.revision === ws.revision ? diff.diff : null}
      onDismissDiff={() => setDiff(null)}
    />
  );

  return (
    <div className="si-studio">
      <PageActions>{nextButton}</PageActions>
      <div className="si-studio-head">
        <button type="button" className="icon-btn" title="Все шаблоны" aria-label="Все шаблоны" onClick={onBack}>
          <Icon name="arrowLeft" size={18} />
        </button>
        <div className="si-studio-title">
          <b>{ws.si_type.name}</b>
          <span className="sub">
            <span className="data">№ {ws.si_type.registry_number}</span>
            {ws.si_type.manufacturer ? ` · ${ws.si_type.manufacturer}` : ""}
            {ws.si_type.needs_review ? " · тип ждёт проверки" : ""}
          </span>
        </div>
        {!phone && (
          <Stepper
            steps={STUDIO_STEPS}
            current={published ? 3 : 2}
            onPick={onStepBack ? (i) => (i < 2 ? onStepBack(i as 0 | 1) : setPublishOpen(false)) : undefined}
            className="compact"
          />
        )}
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
        <span className={`pill ${STATUS_PILL[status]}`} title={ws.draft ? `Черновик, редакция ${ws.revision}` : undefined}>
          {STATUS_LABEL[status]}
          {ws.draft ? <span className="data"> · ред. {ws.revision}</span> : null}
        </span>
      </div>

      {error && <div className="assistant-alert"><Alert onClose={() => setError("")}>{error}</Alert></div>}
      {handoffState && (
        <div className="assistant-alert">
          <Alert tone="info">
            {handoffState === "sending"
              ? "Передаю ассистенту ваши файлы и пожелание…"
              : `Ассистент собирает первый черновик по документам ФИФ. Ваши файлы (${handoffRef.current?.files.length ?? 0}) и пожелание передам, как только он закончит.`}
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
                { value: "protocol", label: phone ? "Протокол" : `Протокол${ws.draft ? ` · ред. ${ws.revision}` : ""}`, icon: "doc", count: diff ? diff.diff.changed.size : undefined },
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

      <PublishSheet
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        ws={ws}
        template={template}
        isReviewer={isReviewer}
        onDone={(data) => {
          setWs(data);
          lastRef.current = data;
          dropTemplatesCache();
          onChanged();
        }}
        go={go}
      />
    </div>
  );
}

function StudioSkeleton() {
  return (
    <div className="si-studio-skel" role="status" aria-label="Открываю рабочее место">
      <div className="si-studio-head">
        <Skeleton width={28} height={28} radius={8} />
        <span className="grow"><Skeleton width="40%" height={15} /><Skeleton width="25%" height={11} style={{ marginTop: 6 }} /></span>
      </div>
      <div className="tpl-split">
        <div className="si-skel-pane"><SkeletonText lines={4} /><SkeletonText lines={3} /></div>
        <div className="si-skel-pane"><Skeleton height="70%" radius={10} /></div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Шаг 4: проверка и публикация
// ---------------------------------------------------------------------------
function PublishSheet({
  open, onClose, ws, template, isReviewer, onDone, go,
}: {
  open: boolean;
  onClose: () => void;
  ws: TemplateWorkspace;
  template: TemplateDigest | null;
  isReviewer: boolean;
  onDone: (ws: TemplateWorkspace) => void;
  go: Go;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const published = ws.status === "published" && !ws.draft;
  const status = workspaceStatus(ws);

  const checks = useMemo(() => {
    if (!template) return [];
    const rows: { ok: boolean | null; label: string; text: string }[] = [
      { ok: Boolean(template.method), label: "Методика", text: template.method || "не указана — попросите ассистента сослаться на МП" },
      { ok: template.operations.length > 0, label: "Операции поверки", text: template.operations.length ? `${template.operations.length}: ${template.operations.slice(0, 3).join("; ")}${template.operations.length > 3 ? "…" : ""}` : "нет" },
      { ok: template.modes.length > 0, label: "Поверяемые точки", text: template.modes.join("; ") || "нет" },
      { ok: template.characteristics.length > 0, label: "Характеристики", text: template.characteristics.join("; ") || "нет" },
      { ok: template.kits.length > 0 ? true : null, label: "Наборы эталонов", text: template.kits.length ? template.kits.join("; ") : "не выбраны — поверитель отметит эталоны сам" },
      {
        ok: template.check == null ? null : template.check.ok,
        label: "Сверка формул с примером",
        text: template.check == null ? "примера в методике нет — метролог сверяет формулы с МП" : template.check.ok ? "формулы сходятся" : "есть расхождения — попросите ассистента найти причину",
      },
    ];
    return rows;
  }, [template]);

  const publish = async () => {
    if (busy || published) return;
    setBusy(true);
    setError("");
    try {
      const data = await publishWorkspace(ws.id);
      onDone(data);
      toast.success("Шаблон опубликован", {
        description: data.published?.needs_review ? "Ждёт проверки метролога — до неё протокол не подписывается" : "Проверен — по типу можно создавать протоколы",
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    const pub = ws.published;
    if (!pub) return;
    setBusy(true);
    setError("");
    try {
      await approveVerificationTemplate(pub.id);
      onDone({ ...ws, published: { ...pub, needs_review: false } });
      toast.success("Шаблон проверен", { description: "Протоколы по нему можно подписывать" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useHotkey("mod+enter", () => void (published ? (isReviewer && status === "review" ? approve() : undefined) : publish()), {
    allowInInputs: true, enabled: open, global: true,
  });

  const footer = published ? (
    <div className="si-step-actions">
      <button type="button" onClick={onClose}>Остаться в студии</button>
      {isReviewer && status === "review" && (
        <button type="button" className="primary" disabled={busy} onClick={() => void approve()}>
          <Icon name="check" size={16} /> Проверен — всё верно <Kbd keys="mod+enter" className="on-accent kbd-hint" />
        </button>
      )}
      <button type="button" className={isReviewer && status === "review" ? "" : "primary"} onClick={() => go("intake")}>
        Создать протоколы <Icon name="arrowRight" size={16} />
      </button>
    </div>
  ) : (
    <div className="si-step-actions">
      <button type="button" onClick={onClose}>Вернуться к шаблону</button>
      <button type="button" className="primary" disabled={busy || !ws.draft || ws.si_type.is_water} onClick={() => void publish()}>
        {busy ? "Публикую…" : "Опубликовать"} <Kbd keys="mod+enter" className="on-accent kbd-hint" />
      </button>
    </div>
  );

  return (
    <Sheet
      open={open}
      onClose={onClose}
      size="md"
      title={published ? "Шаблон действует" : "Проверка и публикация"}
      subtitle={`${ws.si_type.name} · № ${ws.si_type.registry_number}`}
      footer={footer}
    >
      <div className="si-publish">
        {published ? (
          <>
            <div className={`si-publish-state ${status}`}>
              <Icon name={status === "reviewed" ? "checkCircle" : "clock"} size={22} />
              <div>
                <b>{status === "reviewed" ? "Проверен метрологом" : "Ждёт проверки метролога"}</b>
                <p className="sub">
                  {status === "reviewed"
                    ? "По этому типу в «Приёмках» создаются протоколы по шаблону и подписываются."
                    : isReviewer
                      ? "Сверьте шаблон с методикой (вкладка «Состав» и пустой протокол) и отметьте «Проверен»."
                      : "Протоколы по шаблону уже создаются, но подписать их можно после проверки метролога."}
                </p>
              </div>
            </div>
            <p className="sub">Новые правки в чате пойдут в черновик и вступят в силу после следующего «Продолжить».</p>
          </>
        ) : (
          <>
            <p className="sub">
              После публикации шаблон начнёт действовать: в «Приёмках» по этому типу протокол пойдёт по нему.{" "}
              {isReviewer ? "Вы метролог — шаблон сразу будет отмечен проверенным." : "Метролог проверит его; до проверки протокол по шаблону не подписывается."}
            </p>
            {ws.si_type.is_water && <Alert tone="warn">Тип стоит в «Счётчиках воды» — поменяйте «Вид СИ», если это не счётчик воды.</Alert>}
          </>
        )}
        <ul className="si-checks">
          {checks.map((row) => (
            <li key={row.label} className={row.ok === false ? "bad" : row.ok ? "ok" : "neutral"}>
              <Icon name={row.ok === false ? "alert" : row.ok ? "checkCircle" : "info"} size={15} />
              <span>
                <b>{row.label}</b>
                <span className="sub">{row.text}</span>
              </span>
            </li>
          ))}
        </ul>
        {template && (
          <button
            type="button"
            className="sm"
            onClick={() => void downloadFile(workspacePreviewPath(ws.id, "docx", ws.revision, false), `Шаблон протокола — ${ws.si_type.name}.docx`).catch((e) => setError((e as Error).message))}
          >
            <Icon name="download" size={14} /> Пустой протокол в Word
          </button>
        )}
        {error && <Alert onClose={() => setError("")}>{error}</Alert>}
      </div>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Протокол по черновику
// ---------------------------------------------------------------------------
function ProtocolPane({
  ws, template, view, onView, diff, onDismissDiff,
}: {
  ws: TemplateWorkspace;
  template: TemplateDigest | null;
  view: "pdf" | "parts";
  onView: (view: "pdf" | "parts") => void;
  diff: DigestDiff | null;
  onDismissDiff: () => void;
}) {
  const [url, setUrl] = useState("");
  const [pdfError, setPdfError] = useState("");
  const [loading, setLoading] = useState(false);
  const [flash, setFlash] = useState(false);
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
        const first = !urlRef.current;
        urlRef.current = made;
        setUrl(made);
        setPdfError("");
        if (!first) {
          setFlash(true);
          window.setTimeout(() => setFlash(false), 1400);
        }
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
          {ws.agent_busy && (
            <div className="si-wait-skel" aria-hidden="true">
              <Skeleton height={12} width="70%" />
              <Skeleton height={10} />
              <Skeleton height={10} width="85%" />
              <Skeleton height={60} radius={6} />
            </div>
          )}
          {ws.documents && ws.documents.length > 0 && <Documents docs={ws.documents} />}
        </div>
      </div>
    );
  }

  const which = ws.draft ? `Черновик · ред. ${ws.revision}` : "Действующий шаблон";
  return (
    <div className={`tpl-protocol ${flash ? "si-flash" : ""}`}>
      <div className="tpl-protocol-bar">
        <Segmented
          size="sm"
          value={view}
          onChange={onView}
          options={[
            { value: "pdf", label: "Протокол" },
            { value: "parts", label: "Состав", count: diff ? diff.changed.size : undefined },
          ]}
        />
        <span className="sub grow">
          {which}
          {ws.agent_busy && <> · <span className="spinner sm" /> ассистент правит</>}
          {loading && !ws.agent_busy && " · обновляю…"}
        </span>
        <button
          type="button"
          className="sm"
          title="Скачать пустой протокол в Word"
          onClick={() => void downloadFile(workspacePreviewPath(ws.id, "docx", ws.revision, false), `Шаблон протокола — ${ws.si_type.name}.docx`)}
        >
          <Icon name="download" size={14} /> Word
        </button>
      </div>
      {diff && (
        <div className="si-diff-bar" role="status">
          <Icon name="sparkle" size={14} />
          <span className="grow">
            Ред. {ws.revision}: изменилось — {DIGEST_KEYS.filter((k) => diff.changed.has(k)).map((k) => DIGEST_LABEL[k].toLowerCase()).join(", ")}
          </span>
          {view === "pdf" && <button type="button" className="link" onClick={() => onView("parts")}>Показать</button>}
          <button type="button" className="icon-btn" aria-label="Скрыть" onClick={onDismissDiff}><Icon name="x" size={13} /></button>
        </div>
      )}
      {view === "pdf" ? (
        pdfError ? (
          <div className="tpl-parts">
            <Alert tone="warn">PDF не собрался: {pdfError}. Ниже — состав шаблона; Word скачивается кнопкой выше.</Alert>
            <Parts template={template} ws={ws} diff={diff} />
          </div>
        ) : url ? (
          <iframe className="tpl-pdf" src={`${url}#toolbar=0&navpanes=0&view=FitH`} title="Протокол по шаблону" />
        ) : (
          <div className="tpl-parts"><Skeleton height="100%" radius={8} style={{ minHeight: 360 }} /></div>
        )
      ) : (
        <div className="tpl-parts">
          <Parts template={template} ws={ws} diff={diff} />
        </div>
      )}
    </div>
  );
}

function Parts({ template, ws, diff }: { template: TemplateDigest; ws: TemplateWorkspace; diff: DigestDiff | null }) {
  const rows: [DigestKey, string | string[]][] = [
    ["method", template.method || "—"],
    ["si_kind", template.si_kind || ws.si_type.type_title || "—"],
    ["params", template.params.map((p) => `${p.label}${p.unit ? `, ${p.unit}` : ""} — ${p.from_type ? "из типа" : "вносит поверитель"}`)],
    ["operations", template.operations],
    ["modes", template.modes],
    ["points", template.points.map((p) => (template.repeats && template.repeats > 1 ? `${p} × ${template.repeats}` : p))],
    ["characteristics", template.characteristics],
    ["kits", template.kits.length ? template.kits : ["не выбраны — метрологу добавить в перечень"]],
    ["sources", template.sources],
  ];
  return (
    <>
      <dl className="tpl-dl">
        {rows.map(([key, value]) => {
          const changed = diff?.changed.has(key);
          const added = diff?.added.get(key);
          const removed = diff?.removed.get(key) ?? [];
          return (
            <div key={key} className={changed ? "si-changed" : ""}>
              <dt>{DIGEST_LABEL[key]}</dt>
              <dd>
                {Array.isArray(value) ? (
                  value.length ? (
                    <ul>
                      {value.map((v, i) => <li key={i} className={added?.has(v) ? "si-added" : ""}>{v}</li>)}
                      {removed.map((v, i) => <li key={`r${i}`} className="si-removed">{v}</li>)}
                    </ul>
                  ) : (
                    "—"
                  )
                ) : (
                  <span className={changed ? "si-added" : ""}>{value}</span>
                )}
              </dd>
            </div>
          );
        })}
        <div className={diff?.changed.has("check") ? "si-changed" : ""}>
          <dt>Сверка с примером</dt>
          <dd>{template.check == null ? "нет примера в методике" : template.check.ok ? <span className="pass-text">формулы сходятся</span> : <span className="fail-text">есть расхождения</span>}</dd>
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
