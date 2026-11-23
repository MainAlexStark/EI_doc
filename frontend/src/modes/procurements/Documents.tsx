/** Вкладка «Документы»: что загружено и прочитано, поиск по всем документам сразу,
 *  просмотр текста с подсветкой и переходом к найденному фрагменту. */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  deleteProcurementDocument,
  downloadProcurementDocument,
  uploadProcurementDocument,
} from "../../api";
import { errorText, ruShort } from "../../components/tripUtil";
import { EmptyState } from "../../ui/EmptyState";
import Icon from "../../ui/Icon";
import { confirmDialog } from "../../ui/Modal";
import { Sheet } from "../../ui/Sheet";
import { SkeletonList, SkeletonText } from "../../ui/Skeleton";
import toast from "../../ui/Toast";
import { SearchField } from "../../ui/controls";
import {
  extractDocument,
  fetchDocuments,
  fetchDocumentText,
  searchDocuments,
  type DocumentRow,
  type DocumentText,
  type SearchResponse,
} from "./api";

const DOC_KINDS = [
  { value: "notice", label: "Извещение / ТЗ" },
  { value: "contract", label: "Контракт" },
  { value: "pass_list", label: "Заявка на пропуска" },
  { value: "act", label: "Акт" },
  { value: "invoice", label: "Счёт" },
  { value: "letter", label: "Письмо" },
  { value: "other", label: "Другое" },
];

const POLL_MS = 4000;
const CONTEXT = 1500;

const fileSize = (bytes: number) =>
  bytes > 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} МБ` : bytes > 0 ? `${Math.max(1, Math.round(bytes / 1024))} КБ` : "";

function TextBadge({ doc }: { doc: DocumentRow }) {
  if (doc.text_status === "done") {
    const chars = doc.text_chars ?? 0;
    return <span className="pill ok plain">прочитан{chars ? ` · ${chars > 1000 ? `${Math.round(chars / 1000)} тыс.` : chars} зн.` : ""}</span>;
  }
  if (doc.text_status === "pending") return <span className="pill warn plain"><span className="spinner xs" /> читается</span>;
  if (doc.text_status === "empty") return <span className="pill muted plain" title="Скан без текстового слоя">скан без текста</span>;
  return <span className="pill bad plain" title={doc.text_error}>не прочитан</span>;
}

/** Подсветка всех слов запроса (без учёта регистра и ё/е). */
export function highlight(text: string, query: string, firstRef?: (el: HTMLElement | null) => void): ReactNode {
  const words = query.trim().split(/\s+/).filter((w) => w.length >= 2);
  if (!words.length) return text;
  const pattern = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/[её]/gi, "[её]")).join("|")})`, "gi");
  const parts = text.split(pattern);
  let first = true;
  return parts.map((part, index) => {
    if (index % 2 === 0) return part;
    const ref = first ? firstRef : undefined;
    first = false;
    return <mark key={index} ref={ref}>{part}</mark>;
  });
}

export default function DocumentsTab({ procurementId, canEdit }: { procurementId: number; canEdit: boolean }) {
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [kind, setKind] = useState("notice");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [viewer, setViewer] = useState<{ doc: { id: number; title: string }; offset: number; query: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    fetchDocuments(procurementId)
      .then((list) => {
        setDocs(list);
        setError("");
      })
      .catch((exc) => setError(errorText(exc, "Не удалось загрузить документы")));
  }, [procurementId]);

  useEffect(() => {
    load();
  }, [load]);

  // Документы читаются в фоне — пока хоть один «читается», переспрашиваем.
  const pending = docs?.some((doc) => doc.text_status === "pending");
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [pending, load]);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setFound(null);
      setSearching(false);
      return;
    }
    const abort = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(() => {
      searchDocuments(procurementId, q, abort.signal)
        .then(setFound)
        .catch(() => undefined)
        .finally(() => !abort.signal.aborted && setSearching(false));
    }, 300);
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
  }, [query, procurementId]);

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    const id = toast.info(`Загружаю ${files.length > 1 ? `${files.length} файла` : files[0].name}…`, { duration: 0 });
    try {
      for (const file of files) await uploadProcurementDocument(procurementId, file, kind);
      toast.success("Загружено — текст читается в фоне", { id });
      load();
    } catch (exc) {
      toast.error("Не загрузилось", { id, description: errorText(exc, "Файл больше 40 МБ или нет связи") });
    } finally {
      setUploading(false);
    }
  };

  const reread = async (doc: DocumentRow) => {
    setDocs((list) => list?.map((d) => (d.id === doc.id ? { ...d, text_status: "pending" } : d)) ?? list);
    try {
      const fresh = await extractDocument(doc.id);
      setDocs((list) => list?.map((d) => (d.id === doc.id ? fresh : d)) ?? list);
      if (fresh.text_status === "done") toast.success(`«${doc.title}» прочитан`);
      else toast.info(`«${doc.title}»: ${fresh.text_status_display}`, { description: fresh.text_error || undefined });
    } catch (exc) {
      toast.error("Не удалось прочитать", { description: errorText(exc, "") });
      load();
    }
  };

  const remove = async (doc: DocumentRow) => {
    if (!(await confirmDialog({ title: "Удалить документ?", text: doc.title, ok: "Удалить", danger: true }))) return;
    try {
      await deleteProcurementDocument(doc.id);
      setDocs((list) => list?.filter((d) => d.id !== doc.id) ?? list);
      toast.success("Документ удалён");
    } catch (exc) {
      toast.error("Не удалось удалить", { description: errorText(exc, "") });
    }
  };

  const download = (doc: DocumentRow) =>
    downloadProcurementDocument(doc).catch((exc) => toast.error("Не удалось скачать", { description: errorText(exc, "") }));

  if (error) return <EmptyState tone="error" title="Документы не загрузились" action={{ label: "Повторить", onClick: load }}>{error}</EmptyState>;

  return (
    <div
      className={`pr-docs ${dragOver ? "is-drop" : ""}`}
      onDragOver={canEdit ? (e) => { e.preventDefault(); setDragOver(true); } : undefined}
      onDragLeave={() => setDragOver(false)}
      onDrop={canEdit ? (e) => { e.preventDefault(); setDragOver(false); void upload(Array.from(e.dataTransfer.files)); } : undefined}
    >
      <div className="toolbar">
        <SearchField value={query} onChange={setQuery} placeholder="Найти во всех документах: пропуск, неустойка, Ду 15…" className="grow" />
        {canEdit && (
          <div className="pr-upload">
            <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Вид документа">
              {DOC_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
            <button type="button" disabled={uploading} onClick={() => fileInput.current?.click()}>
              <Icon name="upload" size={15} /> {uploading ? "Загружаю…" : "Загрузить"}
            </button>
            <input ref={fileInput} type="file" multiple hidden onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ""; void upload(files); }} />
          </div>
        )}
      </div>

      {query.trim().length >= 2 && (
        <div className="pr-search-results">
          {searching && !found && <SkeletonText lines={3} />}
          {found && found.results.length === 0 && (
            <p className="sub">Ничего не нашлось{found.unread.length ? ` — ещё не прочитаны: ${found.unread.map((d) => d.title).join(", ")}` : ""}.</p>
          )}
          {found?.results.map((result) => (
            <div className="pr-hit card" key={result.document_id}>
              <div className="pr-hit-head">
                <Icon name="file" size={15} /> <b>{result.title}</b>
                <span className="sub">{result.kind_display} · совпадений {result.hits}</span>
              </div>
              {result.snippets.map((snippet) => (
                <button
                  type="button"
                  key={snippet.offset}
                  className="pr-snippet"
                  onClick={() => setViewer({ doc: { id: result.document_id, title: result.title }, offset: snippet.offset, query: found.query })}
                >
                  {snippet.page != null && <span className="pr-page data">с. {snippet.page}</span>}
                  <span>{highlight(snippet.text, found.query)}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}

      {docs == null && <SkeletonList rows={3} />}
      {docs != null && docs.length === 0 && (
        <EmptyState
          icon="folder"
          title="Документов нет"
          action={canEdit ? { label: "Загрузить файлы", icon: "upload", onClick: () => fileInput.current?.click() } : undefined}
        >
          {canEdit ? "Извещение, ТЗ, контракт, списки на пропуска — pdf, docx, xlsx. Можно перетащить сюда. Текст прочитается сам, по нему ищет человек и ассистент." : "Документы ещё не загружены."}
        </EmptyState>
      )}
      {docs != null && docs.length > 0 && (
        <ul className="pr-doc-list">
          {docs.map((doc) => (
            <li key={doc.id} className="pr-doc">
              <Icon name="file" size={18} />
              <button type="button" className="pr-doc-name link" disabled={doc.text_status !== "done"}
                      title={doc.text_status === "done" ? "Открыть текст" : "Текста нет — скачайте файл"}
                      onClick={() => setViewer({ doc, offset: 0, query: query.trim() })}>
                {doc.title}
              </button>
              <span className="sub pr-doc-meta">{[doc.kind_display, fileSize(doc.size), ruShort(doc.created_at.slice(0, 10)), doc.uploaded_by].filter(Boolean).join(" · ")}</span>
              <TextBadge doc={doc} />
              <span className="pr-doc-actions">
                <button type="button" className="icon-btn sm" aria-label="Скачать" title="Скачать" onClick={() => void download(doc)}><Icon name="download" size={15} /></button>
                {canEdit && doc.text_status !== "pending" && (
                  <button type="button" className="icon-btn sm" aria-label="Прочитать заново" title="Прочитать заново" onClick={() => void reread(doc)}><Icon name="refresh" size={15} /></button>
                )}
                {canEdit && (
                  <button type="button" className="icon-btn sm danger" aria-label="Удалить" title="Удалить" onClick={() => void remove(doc)}><Icon name="trash" size={15} /></button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {canEdit && docs != null && docs.length > 0 && <p className="sub pr-drop-hint">Файлы можно перетащить в этот список.</p>}

      <TextViewer target={viewer} onClose={() => setViewer(null)} />
    </div>
  );
}

function TextViewer({
  target, onClose,
}: {
  target: { doc: { id: number; title: string }; offset: number; query: string } | null;
  onClose: () => void;
}) {
  const [part, setPart] = useState<DocumentText | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const markRef = useRef<HTMLElement | null>(null);

  const loadAt = useCallback((docId: number, offset: number) => {
    setPart(null);
    setError("");
    fetchDocumentText(docId, Math.max(0, offset), 40000)
      .then(setPart)
      .catch((exc) => setError(errorText(exc, "Не удалось загрузить текст")));
  }, []);

  useEffect(() => {
    if (!target) return;
    setFilter(target.query);
    loadAt(target.doc.id, target.offset - CONTEXT);
  }, [target, loadAt]);

  useEffect(() => {
    if (part && markRef.current) markRef.current.scrollIntoView({ block: "center" });
  }, [part]);

  return (
    <Sheet
      open={!!target}
      onClose={onClose}
      size="lg"
      title={target?.doc.title}
      subtitle={part ? `${part.page != null ? `с. ${part.page} · ` : ""}знаки ${part.offset.toLocaleString("ru-RU")}–${(part.offset + part.text.length).toLocaleString("ru-RU")} из ${part.total.toLocaleString("ru-RU")}` : undefined}
      footer={part && (
        <>
          <button type="button" disabled={part.offset === 0} onClick={() => target && loadAt(target.doc.id, part.offset - 40000)}>
            <Icon name="chevronLeft" size={15} /> Раньше
          </button>
          <span className="spacer" />
          <button type="button" disabled={part.next_offset == null} onClick={() => target && part.next_offset != null && loadAt(target.doc.id, part.next_offset)}>
            Дальше <Icon name="chevronRight" size={15} />
          </button>
        </>
      )}
    >
      <SearchField value={filter} onChange={setFilter} placeholder="Подсветить в тексте" />
      {error && <EmptyState tone="error" title="Текст не загрузился" compact>{error}</EmptyState>}
      {!error && !part && <SkeletonText lines={12} />}
      {part && (
        <pre className="pr-doc-text">{highlight(part.text, filter, (el) => { markRef.current = el; })}</pre>
      )}
    </Sheet>
  );
}
