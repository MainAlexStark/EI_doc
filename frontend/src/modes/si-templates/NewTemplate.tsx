/** Шаги 1–2 нового шаблона: найти тип (ФИФ) → документы (методика и описание типа из ФИФ + свои файлы).
 *  После шага 2 режим открывает рабочее место — ассистент получает методику и документы типа. */

import { useEffect, useRef, useState } from "react";
import { fetchFifCard, fetchSiTypeDocuments, openFifDocument, type FifDocument, type SiTypeSuggestion } from "../../api";
import type { TemplateWorkspace } from "../../assistant/api";
import FifTypeSearch from "../../components/FifTypeSearch";
import type { SiTypeDraft } from "../../components/SiTypeForm";
import { useHotkey } from "../../ui/Hotkeys";
import Icon from "../../ui/Icon";
import Kbd from "../../ui/Kbd";
import { Skeleton } from "../../ui/Skeleton";
import { Alert } from "../../ui/controls";
import { STATUS_LABEL, STATUS_PILL, workspaceStatus } from "./api";

export const FILE_ACCEPT = ".pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md,.json,.html,image/*";

export type TypePick = {
  /** Уже есть в справочнике — id типа; новый из ФИФ — uuid карточки. */
  si_type?: number;
  fif_uuid?: string;
  name: string;
  registry_number: string;
  manufacturer: string;
  ambiguous?: boolean;
};

export type OpenRequest = { si_type?: number; fif_uuid?: string; name?: string; files: File[]; note: string };

const fromLocal = (item: SiTypeSuggestion): TypePick => ({
  si_type: item.si_type_id ?? undefined,
  name: item.name,
  registry_number: item.registry_number,
  manufacturer: item.manufacturer,
  ambiguous: item.ambiguous,
});
const fromFif = (draft: SiTypeDraft): TypePick => ({
  fif_uuid: draft.fif_uuid,
  name: draft.name ?? "",
  registry_number: draft.registry_number ?? "",
  manufacturer: draft.manufacturer ?? "",
});

export default function NewTemplate({
  workspaces,
  step,
  pick,
  onStep,
  onPick,
  onOpenWorkspace,
  onStart,
  opening,
}: {
  workspaces: TemplateWorkspace[];
  step: 0 | 1;
  pick: TypePick | null;
  onStep: (step: 0 | 1) => void;
  onPick: (pick: TypePick) => void;
  onOpenWorkspace: (id: number) => void;
  onStart: (request: OpenRequest) => void;
  opening: boolean;
}) {
  const [error, setError] = useState("");
  if (step === 0 || !pick) {
    const recent = workspaces.slice(0, 6);
    return (
      <div className="si-new">
        <div className="si-new-head">
          <h2>Какой тип СИ?</h2>
          <p className="muted">
            Номер в Госреестре («77777-20»), обозначение с корпуса или изготовитель. Заведённые у нас типы — сверху; если
            названия совпадают, сверьте изготовителя.
          </p>
        </div>
        {error && <Alert onClose={() => setError("")}>{error}</Alert>}
        <div className="card si-search-card">
          <FifTypeSearch
            autoFocus
            label="Тип СИ в Госреестре (ФИФ)"
            inputId="si-tpl-type-search"
            onPickFif={(draft) => {
              if (!draft.fif_uuid) {
                setError("Шаблон делается по документам из Госреестра — выберите тип из подсказок ФИФ. Тип не из реестра заводит метролог в «Типах СИ».");
                return;
              }
              onPick(fromFif(draft));
              onStep(1);
            }}
            onPickLocal={(item) => {
              if (!item.si_type_id) return;
              const own = workspaces.find((w) => w.si_type.id === item.si_type_id);
              if (own) return onOpenWorkspace(own.id);
              onPick(fromLocal(item));
              onStep(1);
            }}
          />
        </div>
        {recent.length > 0 && (
          <div className="si-recent">
            <div className="si-section-title">Или продолжить начатое</div>
            <div className="si-recent-list">
              {recent.map((w) => {
                const status = workspaceStatus(w);
                return (
                  <button key={w.id} type="button" className="si-recent-item" onClick={() => onOpenWorkspace(w.id)}>
                    <b>{w.si_type.name}</b>
                    <span className="data sub">№ {w.si_type.registry_number}</span>
                    <span className={`pill ${STATUS_PILL[status]}`}>{STATUS_LABEL[status]}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    );
  }
  return <DocumentsStep pick={pick} onBack={() => onStep(0)} onStart={onStart} opening={opening} onRedirect={onPick} />;
}

// ---------------------------------------------------------------------------
function DocumentsStep({
  pick, onBack, onStart, opening, onRedirect,
}: {
  pick: TypePick;
  onBack: () => void;
  onStart: (request: OpenRequest) => void;
  opening: boolean;
  onRedirect: (pick: TypePick) => void;
}) {
  const [docs, setDocs] = useState<FifDocument[] | null>(null);
  const [already, setAlready] = useState<{ id: number; name: string; needs_review: boolean }[]>([]);
  const [docsError, setDocsError] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [note, setNote] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    setDocs(null);
    setDocsError("");
    const load = pick.si_type
      ? fetchSiTypeDocuments(pick.si_type).then((list) => ({ documents: list, already_added: [] as typeof already }))
      : fetchFifCard(pick.fif_uuid ?? "");
    load
      .then((card) => {
        if (cancelled) return;
        setDocs(card.documents);
        setAlready(card.already_added ?? []);
      })
      .catch((e) => {
        if (cancelled) return;
        setDocs([]);
        setDocsError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [pick.si_type, pick.fif_uuid]);

  const start = () => {
    if (opening) return;
    onStart({ si_type: pick.si_type, fif_uuid: pick.fif_uuid, name: pick.name, files, note: note.trim() });
  };
  useHotkey("mod+enter", start, { allowInInputs: true, description: "Начать работу с ассистентом", group: "Шаблоны поверки" });

  const addFiles = (list: FileList | null) => list && setFiles((prev) => [...prev, ...Array.from(list)]);
  const method = docs?.filter((d) => d.kind === "meth") ?? [];
  const spec = docs?.filter((d) => d.kind === "spec") ?? [];

  return (
    <div className="si-new">
      <div className="si-new-head">
        <button type="button" className="link si-back" onClick={onBack}>
          <Icon name="arrowLeft" size={15} /> Другой тип
        </button>
        <h2>{pick.name}</h2>
        <p className="muted">
          <span className="data">№ {pick.registry_number || "—"}</span> · {pick.manufacturer || "изготовитель не указан"}
          {pick.si_type ? " · уже в справочнике" : " · новый тип — заведём из Госреестра, метролог проверит"}
        </p>
        {pick.ambiguous && (
          <Alert tone="warn">Такое название есть у нескольких типов — проверьте номер в Госреестре и изготовителя.</Alert>
        )}
        {already.length > 0 && !pick.si_type && (
          <Alert tone="info">
            Этот тип уже заведён у нас:{" "}
            {already.map((a, i) => (
              <span key={a.id}>
                {i > 0 && ", "}
                <button type="button" className="link" onClick={() => onRedirect({ ...pick, si_type: a.id, fif_uuid: undefined, name: a.name })}>
                  «{a.name}»
                </button>
                {a.needs_review && " (ждёт проверки)"}
              </span>
            ))}{" "}
            — шаблон будет к нему.
          </Alert>
        )}
      </div>

      <div className="si-docs-grid">
        <section className="card si-docs">
          <div className="si-section-title"><Icon name="book" size={15} /> Из Госреестра (ФИФ)</div>
          {docs === null ? (
            <div className="si-doc-skel">
              <Skeleton height={34} radius={8} />
              <Skeleton height={34} radius={8} width="80%" />
            </div>
          ) : (
            <>
              <DocGroup title="Методика поверки" docs={method} empty="Методики в карточке ФИФ нет — приложите её файлом справа, иначе ассистенту не на что опереться." />
              <DocGroup title="Описание типа" docs={spec} empty="Описания типа в карточке нет." />
              {docsError && (
                <p className="hint warn-text">
                  Не удалось получить документы: {docsError}. Ассистент попробует скачать их сам, когда начнёт работу.
                </p>
              )}
            </>
          )}
        </section>

        <section
          className={`card si-docs si-drop ${dragOver ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            addFiles(e.dataTransfer.files);
          }}
        >
          <div className="si-section-title"><Icon name="upload" size={15} /> Свои файлы</div>
          <p className="sub">
            Новая редакция методики, пример заполненного протокола, изменения к МП, РЭ. Перетащите сюда или выберите.
          </p>
          {files.length > 0 && (
            <ul className="si-files">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`}>
                  <Icon name="file" size={14} /> <span className="grow">{file.name}</span>
                  <span className="sub data">{Math.max(1, Math.round(file.size / 1024))} КБ</span>
                  <button type="button" className="icon-btn" aria-label="Убрать" onClick={() => setFiles(files.filter((_, j) => j !== index))}>
                    <Icon name="x" size={13} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <input ref={fileInput} type="file" multiple hidden accept={FILE_ACCEPT} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
          <button type="button" className="sm" onClick={() => fileInput.current?.click()}>
            <Icon name="paperclip" size={14} /> Выбрать файлы
          </button>
        </section>
      </div>

      <div className="field si-note">
        <label htmlFor="si-tpl-note">Что важно учесть (необязательно)</label>
        <textarea
          id="si-tpl-note"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="например: поверяем только периодически; точки 25, 50, 75 % ВПИ; эталон — МО-05М"
        />
      </div>

      <div className="si-step-actions">
        <button type="button" onClick={onBack}>Назад</button>
        <button type="button" className="primary" disabled={opening} onClick={start}>
          {opening ? "Передаю ассистенту…" : "Начать работу с ассистентом"}
          {!opening && <Icon name="arrowRight" size={16} strokeWidth={2.2} />}
          <Kbd keys="mod+enter" className="on-accent kbd-hint" />
        </button>
      </div>
    </div>
  );
}

function DocGroup({ title, docs, empty }: { title: string; docs: FifDocument[]; empty: string }) {
  const [error, setError] = useState("");
  return (
    <div className="si-doc-group">
      <div className="sub">{title}</div>
      {docs.length === 0 ? (
        <p className="hint">{empty}</p>
      ) : (
        docs.map((doc) => (
          <button
            key={doc.id}
            type="button"
            className="si-doc"
            onClick={() => {
              setError("");
              openFifDocument(doc).catch((e) => setError((e as Error).message));
            }}
          >
            <Icon name="doc" size={15} />
            <span className="grow">{doc.title}</span>
            {doc.error ? (
              <span className="pill warn" title={doc.error}>не скачался</span>
            ) : doc.downloaded ? (
              <span className="pill ok">у нас</span>
            ) : (
              <span className="pill muted">в ФГИС</span>
            )}
          </button>
        ))
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
